# Proposal

## Why

Напоминание живёт как один запуск `send_message.py`: расписание снаружи процесса, после попытки скрипт завершается, а в группу уходит только статический текст из `message.json`. Нужен постоянный контейнер, который сам стреляет в часы `SEND_HOURS`, и одно сводное сообщение со списком незакрытых задач Jira.

## What Changes

- Точка входа остаётся `send_message.py`. Процесс после старта не завершается и держит расписание сам.
- Отправка идёт по cron-расписанию часов из `SEND_HOURS` в часовом поясе `TZ` (например, 19:00 `Asia/Bishkek`), без поминутного тикера.
- Если процесс был выключен в момент слота, но календарный день в `TZ` ещё тот же и этот слот не отправлен — сообщение уходит при старте. Слоты предыдущих дней не догоняются: процесс ждёт следующий час из `SEND_HOURS`.
- Ключ идемпотентности остаётся `YYYY-MM-DDTHH` в `data/sent.json`. На каждый такой слот — одно сводное сообщение. Несколько часов в `SEND_HOURS` — несколько независимых слотов одних суток.
- **BREAKING:** текст больше не равен `message.json`. Базовый текст остаётся заголовком, ниже добавляется список незакрытых задач Jira с исполнителями и пингами Telegram.
- Jira читается через REST и уже подключённый `requests`. JQL: `project = <JIRA_BOARD_PROJECT_KEY> AND status = "In Progress" ORDER BY created DESC`. В сводку попадает только статус In Progress.
- Пинги берутся из `users_map.json`: отображаемое имя в Jira → `@telegram_username`.
- В `.env` добавляются `JIRA_URL`, `JIRA_USER`, `JIRA_API_TOKEN`, `JIRA_BOARD_PROJECT_KEY`. В `requirements.txt` — `apscheduler>=3.10.0`. В `docker-compose.yml` монтируется `./users_map.json:/app/users_map.json:ro`.
- **BREAKING:** внешний cron больше не должен вызывать скрипт. Иначе два планировщика делят одни и те же слоты.

## Capabilities

### New Capabilities

- `reminder-schedule`: постоянный процесс, расписание по `SEND_HOURS` и `TZ`, догон слота в те же сутки и запрет догона на следующий день, ключ `YYYY-MM-DDTHH`.
- `jira-digest`: сборка одного сообщения из базового текста `message.json`, незакрытых задач Jira и пингов из `users_map.json`.

### Modified Capabilities

- Нет. В `openspec/specs/` требований ещё нет.

## Impact

- Код: `send_message.py`. Имя файла и `CMD` образа не меняются.
- Зависимости: `apscheduler` 3.x. Отдельный клиент Jira не добавляется.
- Конфигурация: `.env` / `.env.example`, `docker-compose.yml`, пример `users_map.json`. Секреты по-прежнему не попадают в образ.
- Состояние: тот же том `reminder-data` и файл `data/sent.json`. Уже записанные ключи слотов остаются валидными.
- Внешние системы: Telegram Bot API `sendMessage`; Jira Cloud `POST /rest/api/3/search/jql` с basic auth (пользователь + API token).
- Документация: `README.md` описывает одноразовый запуск и статический текст — его нужно привести к новому поведению.
- Эксплуатация: на сервере понадобятся четыре переменные Jira и файл `users_map.json` рядом с `message.json`. Без них сводка не отправляется.
