import html
import json
import logging
import os
import re
import signal
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_HOURS = (19, 20, 21)
STATE_KEEP_DAYS = 7
TELEGRAM_TEXT_LIMIT = 4096
JIRA_PAGE_SIZE = 50
RETRY_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 30
MISFIRE_GRACE_SECONDS = 300
EMPTY_ISSUES_LINE = "Незакрытых задач нет."
UNASSIGNED_LABEL = "без исполнителя"
TRUNCATION_MARK = "… список обрезан, показаны не все задачи."
PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
SEARCH_PATH = "/rest/api/3/search/jql"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("reminder")

PostFunc = Callable[..., requests.Response]
SendFunc = Callable[[str, str, dict], None]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class Settings:
    token: str
    chat_id: str
    tz: ZoneInfo
    hours: tuple[int, ...]
    jira_url: str
    jira_user: str
    jira_token: str
    project_key: str
    message_path: Path
    users_map_path: Path
    state_path: Path


@dataclass(frozen=True)
class Issue:
    key: str
    summary: str
    display_name: str | None


def load_settings(
    environ: Mapping[str, str] | None = None,
    base_dir: Path | None = None,
) -> Settings:
    env = os.environ if environ is None else environ
    root = BASE_DIR if base_dir is None else base_dir
    token = env.get("BOT_TOKEN", "").strip()
    chat_id = env.get("CHAT_ID", "").strip()
    tz_name = env.get("TZ", "").strip()

    if not token or not chat_id:
        raise SystemExit("В .env нужны BOT_TOKEN и CHAT_ID")
    if not tz_name:
        raise SystemExit("В .env нужен TZ, например Asia/Bishkek")

    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"Неизвестный часовой пояс TZ={tz_name}") from exc

    jira_url, jira_user, jira_token, project_key = require_jira(
        env.get("JIRA_URL", ""),
        env.get("JIRA_USER", ""),
        env.get("JIRA_API_TOKEN", ""),
        env.get("JIRA_BOARD_PROJECT_KEY", ""),
    )
    return Settings(
        token=token,
        chat_id=chat_id,
        tz=tz,
        hours=parse_hours(env.get("SEND_HOURS", "")),
        jira_url=jira_url,
        jira_user=jira_user,
        jira_token=jira_token,
        project_key=project_key,
        message_path=Path(env.get("MESSAGE_PATH", str(root / "message.json"))),
        users_map_path=Path(env.get("USERS_MAP_PATH", str(root / "users_map.json"))),
        state_path=Path(env.get("STATE_PATH", str(root / "data" / "sent.json"))),
    )


def require_jira(url: str, user: str, token: str, project_key: str) -> tuple[str, str, str, str]:
    if not user.strip() or not token.strip():
        raise SystemExit("В .env нужны JIRA_USER и JIRA_API_TOKEN")
    normalized = normalize_jira_url(url)
    key = project_key.strip()
    if not PROJECT_KEY_RE.fullmatch(key):
        raise SystemExit(
            "JIRA_BOARD_PROJECT_KEY должен начинаться с буквы и содержать только буквы, цифры и _"
        )
    return normalized, user.strip(), token.strip(), key


def normalize_jira_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SystemExit("JIRA_URL должен быть https-адресом сайта Jira")
    if parsed.username or parsed.password:
        raise SystemExit("JIRA_URL не должен содержать логин или пароль")
    return url


def parse_hours(raw_hours: str) -> tuple[int, ...]:
    raw_hours = raw_hours.strip()
    if not raw_hours:
        return DEFAULT_HOURS
    hours: list[int] = []
    for part in raw_hours.split(","):
        part = part.strip()
        if not part.isdigit() or not 0 <= int(part) <= 23:
            raise SystemExit("SEND_HOURS должен быть списком часов 0-23, например 19,20,21")
        hours.append(int(part))
    if not hours:
        raise SystemExit("SEND_HOURS не содержит ни одного часа")
    return tuple(sorted(set(hours)))


def read_message(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Нет файла сообщения: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"message.json не является JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("message.json должен быть объектом с полем text")

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("В message.json поле text должно быть непустой строкой")

    message = {"text": text}
    parse_mode = payload.get("parse_mode")
    if isinstance(parse_mode, str) and parse_mode.strip():
        message["parse_mode"] = parse_mode.strip()
    return message


def load_users_map(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Нет файла карты пользователей: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"users_map.json не является JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("users_map.json должен быть объектом «имя в Jira → пинг»")
    mapping: dict[str, str] = {}
    for name, mention in payload.items():
        if not isinstance(name, str) or not isinstance(mention, str) or not mention.strip():
            raise RuntimeError("users_map.json должен быть объектом «имя в Jira → пинг»")
        mapping[name] = mention
    return mapping


def load_sent(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Не удалось прочитать %s: %s", path, exc)
        return set()
    sent = data.get("sent", []) if isinstance(data, dict) else []
    if not isinstance(sent, list):
        return set()
    return {item for item in sent if isinstance(item, str)}


def save_sent(path: Path, sent: set[str], now: datetime) -> None:
    cutoff = (now.date() - timedelta(days=STATE_KEEP_DAYS)).isoformat()
    fresh = sorted(item for item in sent if item[:10] >= cutoff)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sent": fresh}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def slot_key(day: datetime, hour: int) -> str:
    return f"{day.date().isoformat()}T{hour:02d}"


def due_slot_keys(now: datetime, hours: tuple[int, ...], sent: set[str]) -> list[str]:
    keys: list[str] = []
    for hour in sorted(set(hours)):
        if hour > now.hour:
            continue
        key = slot_key(now, hour)
        if key in sent:
            continue
        keys.append(key)
    return keys


def _show(value: str, parse_mode: str | None) -> str:
    if parse_mode == "HTML":
        return html.escape(value, quote=False)
    return value


def format_issue(
    issue: Issue,
    users_map: Mapping[str, str],
    jira_url: str,
    parse_mode: str | None,
) -> str:
    link = f"{jira_url}/browse/{issue.key}"
    if issue.display_name is None:
        assignee = f"Исполнитель: {_show(UNASSIGNED_LABEL, parse_mode)}"
    else:
        name = _show(issue.display_name, parse_mode)
        mention = users_map.get(issue.display_name)
        if mention:
            assignee = f"Исполнитель: {name} {mention}"
        else:
            assignee = f"Исполнитель: {name}"
    return "\n".join(
        (
            f"{_show(issue.key, parse_mode)}: {_show(issue.summary, parse_mode)}",
            assignee,
            _show(link, parse_mode),
        )
    )


def _join(header: str, blocks: list[str]) -> str:
    parts = [header, *blocks]
    return "\n\n".join(parts)


def _fit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    room = limit - len(TRUNCATION_MARK) - 2
    if room < 1:
        return TRUNCATION_MARK[:limit]
    return f"{text[:room].rstrip()}\n\n{TRUNCATION_MARK}"[:limit]


def build_digest(
    header_text: str,
    issues: list[Issue],
    users_map: Mapping[str, str],
    jira_url: str,
    parse_mode: str | None = None,
    limit: int = TELEGRAM_TEXT_LIMIT,
) -> tuple[str, bool]:
    header = header_text.rstrip()
    if not issues:
        return _fit(f"{header}\n\n{EMPTY_ISSUES_LINE}", limit), False

    blocks = [format_issue(issue, users_map, jira_url, parse_mode) for issue in issues]
    selected: list[str] = []
    for block in blocks:
        trial = _join(header, [*selected, block])
        if len(trial) <= limit:
            selected.append(block)
            continue
        break
    truncated = len(selected) < len(blocks)
    if not truncated:
        return _join(header, selected), False

    while selected and len(_join(header, selected)) + 2 + len(TRUNCATION_MARK) > limit:
        selected.pop()
    body = _join(header, selected) if selected else header
    if len(body) + 2 + len(TRUNCATION_MARK) <= limit:
        return f"{body}\n\n{TRUNCATION_MARK}", True
    return _fit(body, limit), True


def jira_jql(project_key: str) -> str:
    return f'project = {project_key} AND status = "In Progress" ORDER BY created DESC'


def parse_issue(raw: object) -> Issue:
    if not isinstance(raw, dict):
        raise RuntimeError("Jira вернула задачу в неожиданном виде")
    key = raw.get("key")
    fields = raw.get("fields") if isinstance(raw.get("fields"), dict) else {}
    summary = fields.get("summary")
    if not isinstance(key, str) or not key or not isinstance(summary, str):
        raise RuntimeError("Jira вернула задачу без ключа или названия")
    assignee = fields.get("assignee")
    display_name = None
    if isinstance(assignee, dict):
        name = assignee.get("displayName")
        if isinstance(name, str) and name:
            display_name = name
    elif assignee is not None:
        raise RuntimeError("Jira вернула исполнителя в неожиданном виде")
    return Issue(key=key, summary=summary, display_name=display_name)


def _jira_payload(response: requests.Response) -> dict:
    if response.status_code != 200:
        raise RuntimeError(f"Jira ответила HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Jira вернула не JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Jira вернула не объект")
    return data


def fetch_open_issues(
    post: PostFunc,
    *,
    jira_url: str,
    user: str,
    token: str,
    project_key: str,
    header: str,
    users_map: Mapping[str, str],
    parse_mode: str | None,
) -> list[Issue]:
    url = f"{jira_url}{SEARCH_PATH}"
    auth = HTTPBasicAuth(user, token)
    body_base = {
        "jql": jira_jql(project_key),
        "fields": ["summary", "assignee"],
        "maxResults": JIRA_PAGE_SIZE,
    }
    collected: list[Issue] = []
    page_token: str | None = None
    while True:
        body = dict(body_base)
        if page_token:
            body["nextPageToken"] = page_token
        try:
            response = post(url, json=body, auth=auth, timeout=30)
        except requests.RequestException as exc:
            raise RuntimeError("Не удалось обратиться к Jira") from exc
        data = _jira_payload(response)
        raw_issues = data.get("issues", [])
        if not isinstance(raw_issues, list):
            raise RuntimeError("Jira вернула список задач в неожиданном виде")
        batch = [parse_issue(item) for item in raw_issues]
        collected.extend(batch)
        next_token = data.get("nextPageToken")
        page_token = next_token if isinstance(next_token, str) and next_token else None
        _, truncated = build_digest(header, collected, users_map, jira_url, parse_mode)
        if truncated or not page_token or not batch:
            break
    return collected


def send_message(token: str, chat_id: str, message: dict) -> None:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, **message},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Не удалось обратиться к Telegram") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Telegram вернул не JSON, HTTP {response.status_code}") from exc
    if not data.get("ok"):
        description = data.get("description", data)
        raise RuntimeError(f"Ошибка Telegram: {description}")


def deliver_slot(
    settings: Settings,
    key: str,
    now: datetime,
    sent: set[str],
    *,
    post: PostFunc,
    send: SendFunc,
    sleeper: Sleeper,
    attempts: int = RETRY_ATTEMPTS,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            message = read_message(settings.message_path)
            users_map = load_users_map(settings.users_map_path)
            parse_mode = message.get("parse_mode")
            issues = fetch_open_issues(
                post,
                jira_url=settings.jira_url,
                user=settings.jira_user,
                token=settings.jira_token,
                project_key=settings.project_key,
                header=message["text"],
                users_map=users_map,
                parse_mode=parse_mode,
            )
            text, truncated = build_digest(
                message["text"],
                issues,
                users_map,
                settings.jira_url,
                parse_mode,
            )
            payload = {"text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            send(settings.token, settings.chat_id, payload)
            sent.add(key)
            save_sent(settings.state_path, sent, now)
            log.info(
                "Слот %s отправлен, задач: %s%s",
                key,
                len(issues),
                ", список обрезан" if truncated else "",
            )
            return
        except (RuntimeError, OSError) as exc:
            last_error = exc
            log.error("Слот %s, попытка %s: %s", key, attempt, exc)
            if attempt < attempts:
                sleeper(RETRY_PAUSE_SECONDS)
    log.error("Слот %s не отправлен: %s", key, last_error)


def run_due(
    settings: Settings,
    now: datetime,
    *,
    post: PostFunc,
    send: SendFunc,
    sleeper: Sleeper,
    attempts: int = RETRY_ATTEMPTS,
) -> None:
    sent = load_sent(settings.state_path)
    for key in due_slot_keys(now, settings.hours, sent):
        deliver_slot(
            settings,
            key,
            now,
            sent,
            post=post,
            send=send,
            sleeper=sleeper,
            attempts=attempts,
        )


def attach_job(scheduler: BlockingScheduler, settings: Settings, job: Callable[[], None]) -> None:
    scheduler.add_job(
        job,
        CronTrigger(
            minute=0,
            hour=",".join(str(hour) for hour in settings.hours),
            timezone=settings.tz,
        ),
        id="digest",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )


def handle_stop(scheduler: BlockingScheduler, signum: int, frame: object) -> None:
    log.info("Остановка")
    if scheduler.running:
        scheduler.shutdown(wait=False)
        return
    raise SystemExit(0)


def serve(
    settings: Settings,
    *,
    post: PostFunc,
    send: SendFunc,
    sleeper: Sleeper = time.sleep,
    install_signals: bool = True,
    scheduler: BlockingScheduler | None = None,
) -> None:
    scheduler = scheduler or BlockingScheduler(timezone=settings.tz)

    def job() -> None:
        run_due(settings, datetime.now(settings.tz), post=post, send=send, sleeper=sleeper)

    attach_job(scheduler, settings, job)
    if install_signals:
        signal.signal(signal.SIGTERM, lambda signum, frame: handle_stop(scheduler, signum, frame))
        signal.signal(signal.SIGINT, lambda signum, frame: handle_stop(scheduler, signum, frame))
    log.info(
        "Расписание: %s, часовой пояс %s",
        ",".join(str(hour) for hour in settings.hours),
        settings.tz.key,
    )
    job()
    scheduler.start()


def main() -> None:
    settings = load_settings()
    serve(settings, post=requests.post, send=send_message)


if __name__ == "__main__":
    main()
