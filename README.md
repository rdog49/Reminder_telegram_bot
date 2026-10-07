# telegram-reminder

Бот каждый день отправляет одно сообщение в рабочую группу Telegram в 19:00, 20:00 и 21:00. Контейнер не завершается. После перезагрузки сервера Docker поднимает его снова, если демон включён в автозагрузку.

Образ собирает GitHub Actions при пуше в `main` и публикует в GitHub Container Registry:

`ghcr.io/rdog49/reminder_telegram_bot:latest`

Сборка идёт под `linux/amd64`. На сервере образ не собирается: `docker-compose.yml` только скачивает его и запускает. Токен, id группы и текст сообщения в образ не входят.

## Что нужно заранее

1. На сервере установлены Docker Engine и плагин Compose. Демон Docker включён в автозагрузку:

```bash
sudo systemctl enable --now docker
```

2. Бот создан в BotFather и добавлен в рабочую группу. У группы есть числовой `CHAT_ID` (у супергруппы он обычно начинается с `-100`).
3. В репозитории на ветке `main` зелёная сборка **Publish image**: https://github.com/rdog49/Reminder_telegram_bot/actions  
   Пока её нет, `docker compose pull` не найдёт образ.

Если `pull` просит логин, пакет ещё приватный. Один раз откройте его в GitHub → Packages у пользователя `rdog49` и поставьте Visibility: Public.

## Поднять на сервере

Репозиторий на сервер клонировать не нужно. Код бота уже внутри образа. На сервере нужен только каталог с тремя файлами: `docker-compose.yml`, `.env` и `message.json`. Образ скачает `docker compose pull`.

```bash
mkdir -p /opt/telegram-reminder
cd /opt/telegram-reminder
```

Создайте `docker-compose.yml`:

```yaml
services:
  reminder:
    image: ghcr.io/rdog49/reminder_telegram_bot:latest
    pull_policy: always
    container_name: telegram-reminder
    init: true
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./message.json:/app/message.json:ro
      - reminder-data:/app/data

volumes:
  reminder-data:
```

Создайте `.env` в этом каталоге:

```env
BOT_TOKEN=123456:ABC...
CHAT_ID=-1001234567890
TZ=Asia/Omsk
SEND_HOURS=19,20,21
```

- `BOT_TOKEN` — токен от BotFather.
- `CHAT_ID` — id группы.
- `TZ` — часовой пояс группы, имя из базы IANA. Для UTC+6 это `Asia/Omsk`.
- `SEND_HOURS` — часы отправки через запятую. Для этого бота: `19,20,21`.

Создайте `message.json` в этом же каталоге до первого запуска. Если файла нет, Docker создаст на его месте каталог, и бот не увидит текст.

```json
{
  "text": "Текст напоминания"
}
```

Необязательное поле `parse_mode` (`HTML` или `Markdown`) включает разметку Telegram.

Запуск:

```bash
docker compose pull
docker compose up -d
```

Проверка:

```bash
docker compose ps
docker compose logs -f
```

В логе при старте есть часовой пояс и время следующего слота. Выход из логов: `Ctrl+C`, контейнер при этом продолжает работать.

## Как он шлёт сообщения

В каждом часе из `SEND_HOURS` сообщение уходит один раз. Если отправка не удалась, попытки повторяются до конца этого часа. Если контейнер в этом часе уже успешно отправил сообщение и потом перезапустился, повторной отправки нет: слот записан в том `reminder-data`. Если контейнер был выключен весь час, этот слот пропускается.

## Обновление

Новая версия кода попадает в реестр после зелёной сборки в `main`. На сервере достаточно скачать новый образ и пересоздать контейнер:

```bash
cd /opt/telegram-reminder
docker compose pull
docker compose up -d
```

Текст в `message.json` бот читает заново при каждой отправке. Для смены текста достаточно сохранить файл.

После правки `.env` контейнер нужно пересоздать:

```bash
docker compose up -d --force-recreate
```

## Остановка

Остановить контейнер и оставить историю слотов:

```bash
docker compose down
```

Том `reminder-data` при этом сохраняется. Следующий `docker compose up -d` продолжит с теми же слотами.
