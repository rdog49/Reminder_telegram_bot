import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

MESSAGE_PATH = Path(os.getenv("MESSAGE_PATH", str(BASE_DIR / "message.json")))
STATE_PATH = Path(os.getenv("STATE_PATH", str(BASE_DIR / "data" / "sent.json")))
DEFAULT_HOURS = (19, 20, 21)
STATE_KEEP_DAYS = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("reminder")


def load_settings() -> tuple[str, str, ZoneInfo, tuple[int, ...]]:
    token = os.getenv("BOT_TOKEN", "").strip()
    chat_id = os.getenv("CHAT_ID", "").strip()
    tz_name = os.getenv("TZ", "").strip()

    if not token or not chat_id:
        raise SystemExit("В .env нужны BOT_TOKEN и CHAT_ID")
    if not tz_name:
        raise SystemExit("В .env нужен TZ, например Asia/Bishkek")

    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise SystemExit(f"Неизвестный часовой пояс TZ={tz_name}") from exc

    raw_hours = os.getenv("SEND_HOURS", "").strip()
    if not raw_hours:
        return token, chat_id, tz, DEFAULT_HOURS

    hours: list[int] = []
    for part in raw_hours.split(","):
        part = part.strip()
        if not part.isdigit() or not 0 <= int(part) <= 23:
            raise SystemExit("SEND_HOURS должен быть списком часов 0-23, например 19,20,21")
        hours.append(int(part))
    if not hours:
        raise SystemExit("SEND_HOURS не содержит ни одного часа")
    return token, chat_id, tz, tuple(sorted(set(hours)))


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


def slot_key(now: datetime) -> str:
    return f"{now.date().isoformat()}T{now.hour:02d}"


def send_message(token: str, chat_id: str, message: dict) -> None:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, **message},
        timeout=30,
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Telegram вернул не JSON, HTTP {response.status_code}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"Ошибка Telegram: {data}")


def main() -> None:
    token, chat_id, tz, hours = load_settings()
    sent = load_sent(STATE_PATH)
    
    now = datetime.now(tz)
    key = slot_key(now)
    
    log.info("Запуск по расписанию для слота %s (TZ: %s)", key, tz.key)

    if key in sent:
        log.info("Слот %s уже был отправлен ранее. Пропуск.", key)
        return

    try:
        message = read_message(MESSAGE_PATH)
        send_message(token, chat_id, message)
        sent.add(key)
        save_sent(STATE_PATH, sent, now)
        log.info("Сообщение успешно отправлено для слота %s", key)
    except (RuntimeError, requests.RequestException) as exc:
        log.error("Ошибка при отправке: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()