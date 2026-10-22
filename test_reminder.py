import json
import signal
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

import apscheduler
import requests
from requests.auth import HTTPBasicAuth

import send_message
from send_message import (
    Issue,
    attach_job,
    build_digest,
    due_slot_keys,
    fetch_open_issues,
    handle_stop,
    load_settings,
    load_users_map,
    require_jira,
    run_due,
    serve,
)

TZ = ZoneInfo("Asia/Bishkek")
TOKEN = "jira-token-secret"


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def issue_raw(key, summary, display_name):
    assignee = None if display_name is None else {"displayName": display_name}
    return {"key": key, "fields": {"summary": summary, "assignee": assignee}}


class JiraSettingsTests(unittest.TestCase):
    def test_empty_token_exits_before_scheduler(self):
        with self.assertRaises(SystemExit) as caught:
            require_jira("https://example.atlassian.net", "user@example.com", "  ", "PROJ")
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_http_url_exits(self):
        with self.assertRaises(SystemExit):
            require_jira("http://example.atlassian.net", "user@example.com", TOKEN, "PROJ")

    def test_project_key_with_quote_exits(self):
        with self.assertRaises(SystemExit):
            require_jira("https://example.atlassian.net/", "user@example.com", TOKEN, 'PR"OJ')

    def test_https_url_loses_trailing_slash(self):
        url, user, token, key = require_jira(
            "https://example.atlassian.net/",
            "user@example.com",
            TOKEN,
            "PROJ",
        )
        self.assertEqual(url, "https://example.atlassian.net")
        self.assertEqual(user, "user@example.com")
        self.assertEqual(token, TOKEN)
        self.assertEqual(key, "PROJ")

    def test_load_settings_rejects_jira_before_hours_are_scheduled(self):
        with self.assertRaises(SystemExit):
            load_settings(
                {
                    "BOT_TOKEN": "bot",
                    "CHAT_ID": "-100",
                    "TZ": "Asia/Bishkek",
                    "SEND_HOURS": "19",
                    "JIRA_URL": "http://example.atlassian.net",
                    "JIRA_USER": "user@example.com",
                    "JIRA_API_TOKEN": TOKEN,
                    "JIRA_BOARD_PROJECT_KEY": "PROJ",
                }
            )


class UsersMapTests(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(RuntimeError):
            load_users_map(Path("no-such-users-map.json"))

    def test_not_an_object(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "users_map.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_users_map(path)

    def test_value_must_be_string(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "users_map.json"
            path.write_text('{"Иван Иванов": 1}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_users_map(path)

    def test_valid_map(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "users_map.json"
            path.write_text('{"Иван Иванов": "@ivan"}', encoding="utf-8")
            self.assertEqual(load_users_map(path), {"Иван Иванов": "@ivan"})


class DigestTests(unittest.TestCase):
    def test_header_and_issue_order(self):
        issues = [
            Issue("PROJ-2", "Вторая", "Иван Иванов"),
            Issue("PROJ-1", "Первая", "Иван Иванов"),
        ]
        text, truncated = build_digest(
            "Сводка дня",
            issues,
            {"Иван Иванов": "@ivan"},
            "https://example.atlassian.net",
        )
        self.assertFalse(truncated)
        self.assertTrue(text.startswith("Сводка дня"))
        self.assertLess(text.index("PROJ-2"), text.index("PROJ-1"))
        self.assertIn("https://example.atlassian.net/browse/PROJ-2", text)
        self.assertIn("@ivan", text)

    def test_mapped_unmapped_and_unassigned(self):
        issues = [
            Issue("PROJ-1", "С пингом", "Иван Иванов"),
            Issue("PROJ-2", "Без карты", "Мария Петрова"),
            Issue("PROJ-3", "Ничья", None),
        ]
        text, _truncated = build_digest(
            "Заголовок",
            issues,
            {"Иван Иванов": "@ivan"},
            "https://example.atlassian.net",
        )
        self.assertIn("Иван Иванов @ivan", text)
        self.assertIn("Мария Петрова", text)
        self.assertNotIn("Мария Петрова @", text)
        unassigned = text.split("PROJ-3", 1)[1]
        self.assertIn("без исполнителя", unassigned)
        self.assertNotIn("@", unassigned)

    def test_empty_issues(self):
        text, truncated = build_digest("Заголовок", [], {}, "https://example.atlassian.net")
        self.assertFalse(truncated)
        self.assertEqual(text, "Заголовок\n\nНезакрытых задач нет.")

    def test_html_escapes_jira_text_and_keeps_mention(self):
        issues = [Issue("PROJ-1", "A < B & C", "Иван Иванов")]
        text, _truncated = build_digest(
            "<b>Заголовок</b>",
            issues,
            {"Иван Иванов": "@ivan"},
            "https://example.atlassian.net",
            "HTML",
        )
        self.assertIn("<b>Заголовок</b>", text)
        self.assertIn("A &lt; B &amp; C", text)
        self.assertIn("@ivan", text)
        self.assertNotIn("A < B", text)

    def test_truncates_to_limit_with_mark(self):
        issues = [Issue(f"P-{index}", "задача " * 12, "Иван Иванов") for index in range(1, 8)]
        text, truncated = build_digest(
            "Сводка",
            issues,
            {"Иван Иванов": "@ivan"},
            "https://example.atlassian.net",
            limit=280,
        )
        self.assertTrue(truncated)
        self.assertTrue(text.startswith("Сводка"))
        self.assertIn("список обрезан", text)
        self.assertLessEqual(len(text), 280)
        self.assertIn("P-1", text)
        self.assertNotIn("P-7", text)


class JiraFetchTests(unittest.TestCase):
    def test_two_pages_use_next_token_and_basic_auth(self):
        pages = [
            {
                "issues": [issue_raw("PROJ-1", "Первая", "Иван Иванов")],
                "nextPageToken": "page-2",
            },
            {"issues": [issue_raw("PROJ-2", "Вторая", None)]},
        ]
        calls = []

        def post(url, json, auth, timeout):
            calls.append((url, json, auth, timeout))
            return FakeResponse(200, pages[len(calls) - 1])

        issues = fetch_open_issues(
            post,
            jira_url="https://example.atlassian.net",
            user="user@example.com",
            token=TOKEN,
            project_key="PROJ",
            header="Заголовок",
            users_map={"Иван Иванов": "@ivan"},
            parse_mode=None,
        )
        self.assertEqual([item.key for item in issues], ["PROJ-1", "PROJ-2"])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("nextPageToken", calls[0][1])
        self.assertEqual(calls[1][1]["nextPageToken"], "page-2")
        self.assertEqual(calls[0][1]["maxResults"], 50)
        self.assertEqual(calls[0][1]["fields"], ["summary", "assignee"])
        self.assertEqual(
            calls[0][1]["jql"],
            'project = PROJ AND status = "In Progress" ORDER BY created DESC',
        )
        self.assertEqual(calls[0][0], "https://example.atlassian.net/rest/api/3/search/jql")
        self.assertIsInstance(calls[0][2], HTTPBasicAuth)
        self.assertEqual(calls[0][2].username, "user@example.com")
        self.assertEqual(calls[0][2].password, TOKEN)

    def test_http_error_hides_token(self):
        def post(url, json, auth, timeout):
            return FakeResponse(401, {"error": TOKEN})

        with self.assertRaises(RuntimeError) as caught:
            fetch_open_issues(
                post,
                jira_url="https://example.atlassian.net",
                user="user@example.com",
                token=TOKEN,
                project_key="PROJ",
                header="Заголовок",
                users_map={},
                parse_mode=None,
            )
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_stops_when_digest_no_longer_fits(self):
        calls = []

        def post(url, json, auth, timeout):
            calls.append(json)
            return FakeResponse(
                200,
                {
                    "issues": [issue_raw("PROJ-1", "очень длинное " * 400, "Иван Иванов")],
                    "nextPageToken": "page-2",
                },
            )

        fetch_open_issues(
            post,
            jira_url="https://example.atlassian.net",
            user="user@example.com",
            token=TOKEN,
            project_key="PROJ",
            header="Заголовок",
            users_map={},
            parse_mode=None,
        )
        self.assertEqual(len(calls), 1)


class ScheduleTests(unittest.TestCase):
    hours = (19, 20, 21)

    def test_late_start_includes_passed_hour_19(self):
        now = datetime(2026, 10, 22, 20, 30, tzinfo=TZ)
        keys = due_slot_keys(now, self.hours, set())
        self.assertEqual(keys, ["2026-10-22T19", "2026-10-22T20"])

    def test_next_day_does_not_include_yesterday(self):
        now = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)
        keys = due_slot_keys(now, self.hours, set())
        self.assertEqual(keys, [])
        self.assertNotIn("2026-10-22T19", keys)

    def test_morning_waits(self):
        now = datetime(2026, 10, 22, 10, 0, tzinfo=TZ)
        self.assertEqual(due_slot_keys(now, self.hours, set()), [])

    def test_later_hour_stays_due_after_earlier_slot_was_sent(self):
        now = datetime(2026, 10, 22, 21, 0, tzinfo=TZ)
        keys = due_slot_keys(now, self.hours, {"2026-10-22T19"})
        self.assertIn("2026-10-22T21", keys)
        self.assertNotIn("2026-10-22T19", keys)
        self.assertEqual(keys, ["2026-10-22T20", "2026-10-22T21"])


class DeliveryTests(unittest.TestCase):
    def settings(self, folder: str):
        root = Path(folder)
        (root / "message.json").write_text(
            json.dumps({"text": "Заголовок", "parse_mode": "HTML"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (root / "users_map.json").write_text('{"Иван Иванов": "@ivan"}', encoding="utf-8")
        return load_settings(
            {
                "BOT_TOKEN": "bot-token-secret",
                "CHAT_ID": "-100",
                "TZ": "Asia/Bishkek",
                "SEND_HOURS": "19,20,21",
                "JIRA_URL": "https://example.atlassian.net",
                "JIRA_USER": "user@example.com",
                "JIRA_API_TOKEN": TOKEN,
                "JIRA_BOARD_PROJECT_KEY": "PROJ",
                "MESSAGE_PATH": str(root / "message.json"),
                "USERS_MAP_PATH": str(root / "users_map.json"),
                "STATE_PATH": str(root / "data" / "sent.json"),
            }
        )

    def empty_post(self, url, json, auth, timeout):
        return FakeResponse(200, {"issues": []})

    def test_failure_does_not_record_slot_and_retries_three_times(self):
        with TemporaryDirectory() as folder:
            settings = self.settings(folder)
            calls = []
            pauses = []

            def send(token, chat_id, message):
                calls.append(message)
                raise RuntimeError("сеть")

            now = datetime(2026, 10, 22, 19, 5, tzinfo=TZ)
            run_due(
                settings,
                now,
                post=self.empty_post,
                send=send,
                sleeper=pauses.append,
                attempts=3,
            )
            self.assertEqual(len(calls), 3)
            self.assertEqual(pauses, [30, 30])
            self.assertFalse(settings.state_path.exists())
            self.assertNotIn("bot-token-secret", " ".join(str(item) for item in calls))

    def test_success_records_sent_json_and_parse_mode(self):
        with TemporaryDirectory() as folder:
            settings = self.settings(folder)
            sent_payloads = []

            def send(token, chat_id, message):
                sent_payloads.append(message)

            now = datetime(2026, 10, 22, 19, 0, tzinfo=TZ)
            run_due(settings, now, post=self.empty_post, send=send, sleeper=lambda _seconds: None)
            self.assertEqual(len(sent_payloads), 1)
            self.assertEqual(sent_payloads[0]["parse_mode"], "HTML")
            self.assertIn("Заголовок", sent_payloads[0]["text"])
            self.assertIn("Незакрытых задач нет.", sent_payloads[0]["text"])
            saved = json.loads(settings.state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved, {"sent": ["2026-10-22T19"]})

    def test_map_is_read_when_the_slot_is_sent(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            settings = load_settings(
                {
                    "BOT_TOKEN": "bot",
                    "CHAT_ID": "-100",
                    "TZ": "Asia/Bishkek",
                    "SEND_HOURS": "19",
                    "JIRA_URL": "https://example.atlassian.net",
                    "JIRA_USER": "user@example.com",
                    "JIRA_API_TOKEN": TOKEN,
                    "JIRA_BOARD_PROJECT_KEY": "PROJ",
                    "MESSAGE_PATH": str(root / "message.json"),
                    "USERS_MAP_PATH": str(root / "users_map.json"),
                    "STATE_PATH": str(root / "data" / "sent.json"),
                }
            )
            now = datetime(2026, 10, 22, 19, 0, tzinfo=TZ)
            run_due(
                settings,
                now,
                post=self.empty_post,
                send=lambda *_args: None,
                sleeper=lambda _seconds: None,
                attempts=1,
            )
            self.assertFalse(settings.state_path.exists())
            (root / "message.json").write_text('{"text": "Уже есть"}', encoding="utf-8")
            (root / "users_map.json").write_text("{}", encoding="utf-8")
            run_due(
                settings,
                now,
                post=self.empty_post,
                send=lambda *_args: None,
                sleeper=lambda _seconds: None,
                attempts=1,
            )
            saved = json.loads(settings.state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["sent"], ["2026-10-22T19"])


class SchedulerTests(unittest.TestCase):
    def test_cron_job_parameters(self):
        settings = load_settings(
            {
                "BOT_TOKEN": "bot",
                "CHAT_ID": "-100",
                "TZ": "Asia/Bishkek",
                "SEND_HOURS": "19,20,21",
                "JIRA_URL": "https://example.atlassian.net",
                "JIRA_USER": "user@example.com",
                "JIRA_API_TOKEN": TOKEN,
                "JIRA_BOARD_PROJECT_KEY": "PROJ",
            }
        )
        scheduler = send_message.BlockingScheduler(timezone=settings.tz)
        attach_job(scheduler, settings, lambda: None)
        job = scheduler.get_jobs()[0]
        self.assertEqual(job.max_instances, 1)
        self.assertTrue(job.coalesce)
        self.assertEqual(job.misfire_grace_time, 300)
        fields = {field.name: str(field) for field in job.trigger.fields}
        self.assertEqual(fields["minute"], "0")
        self.assertEqual(fields["hour"], "19,20,21")

    def test_serve_reaches_scheduler_after_startup_pass(self):
        class FakeScheduler:
            def __init__(self):
                self.started = False
                self.running = False

            def add_job(self, func, trigger, **kwargs):
                self.job = func

            def start(self):
                self.started = True

            def shutdown(self, wait=False):
                self.wait = wait

        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "message.json").write_text('{"text": "Заголовок"}', encoding="utf-8")
            (root / "users_map.json").write_text("{}", encoding="utf-8")
            settings = load_settings(
                {
                    "BOT_TOKEN": "bot",
                    "CHAT_ID": "-100",
                    "TZ": "Asia/Bishkek",
                    "SEND_HOURS": "19",
                    "JIRA_URL": "https://example.atlassian.net",
                    "JIRA_USER": "user@example.com",
                    "JIRA_API_TOKEN": TOKEN,
                    "JIRA_BOARD_PROJECT_KEY": "PROJ",
                    "MESSAGE_PATH": str(root / "message.json"),
                    "USERS_MAP_PATH": str(root / "users_map.json"),
                    "STATE_PATH": str(root / "data" / "sent.json"),
                }
            )
            fake = FakeScheduler()
            serve(
                settings,
                post=lambda url, json, auth, timeout: FakeResponse(200, {"issues": []}),
                send=lambda *_args: None,
                sleeper=lambda _seconds: (_ for _ in ()).throw(AssertionError("пауза не нужна")),
                install_signals=False,
                scheduler=fake,
            )
            self.assertTrue(fake.started)

    def test_stop_handler_shuts_scheduler_down(self):
        class Running:
            running = True
            wait = None

            def shutdown(self, wait=False):
                self.wait = wait

        scheduler = Running()
        handle_stop(scheduler, signal.SIGTERM, None)
        self.assertFalse(scheduler.wait)

        idle = Running()
        idle.running = False
        with self.assertRaises(SystemExit):
            handle_stop(idle, signal.SIGINT, None)


class RuntimeTests(unittest.TestCase):
    def test_apscheduler_major_is_3_and_dockerfile_keeps_entrypoint(self):
        self.assertEqual(apscheduler.__version__.split(".", 1)[0], "3")
        import send_message as module

        self.assertIs(module.BlockingScheduler, send_message.BlockingScheduler)
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn('CMD ["python", "send_message.py"]', dockerfile)

    def test_requests_still_used_for_telegram_wrapper(self):
        self.assertTrue(hasattr(requests, "post"))


if __name__ == "__main__":
    unittest.main()
