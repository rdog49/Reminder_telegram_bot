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

Все команды ниже выполняются на сервере в терминале. Если редактора `nano` нет, поставьте его: `sudo apt update && sudo apt install -y nano`.

Создайте каталог и перейдите в него:

```bash
sudo mkdir -p /opt/telegram-reminder
cd /opt/telegram-reminder
```

Откройте пустой `docker-compose.yml`:

```bash
sudo nano docker-compose.yml
```

Вставьте в открытый файл этот текст целиком. Вставка в терминале — правая кнопка мыши или `Ctrl+Shift+V`.

```yaml
services:
  reminder:
    image: ghcr.io/rdog49/reminder_telegram_bot:latest
    pull_policy: always
    container_name: telegram-reminder
    init: true
    restart: unless-stopped
    security_opt:
      - apparmor=unconfined
    env_file:
      - .env
    volumes:
      - ./message.json:/app/message.json:ro
      - reminder-data:/app/data

volumes:
  reminder-data:
```

Сохраните файл: `Ctrl+O`, затем `Enter`. Закройте редактор: `Ctrl+X`.

Откройте пустой `.env`:

```bash
sudo nano .env
```

Вставьте свои значения. `123456:ABC...` замените на токен от BotFather, `-1001234567890` — на id группы. Для UTC+6 часовой пояс `Asia/Omsk`.

```env
BOT_TOKEN=123456:ABC...
CHAT_ID=-1001234567890
TZ=Asia/Omsk
SEND_HOURS=19,20,21
```

- `BOT_TOKEN` — токен от BotFather.
- `CHAT_ID` — id группы.
- `TZ` — часовой пояс группы, имя из базы IANA.
- `SEND_HOURS` — часы отправки через запятую. Для этого бота: `19,20,21`.

Снова `Ctrl+O`, `Enter`, `Ctrl+X`.

`message.json` нужен до первого запуска. Если файла нет, Docker создаст на его месте каталог, и бот не увидит текст. Откройте его:

```bash
sudo nano message.json
```

Русский текст в `nano` с клавиатуры сервера обычно не набирается. Напишите сообщение на своём компьютере, скопируйте и вставьте в открытый `nano` правой кнопкой мыши или `Ctrl+Shift+V`. Файл выглядит так:

```json
{
  "text": "Текст напоминания"
}
```

Если вставка тоже превращается в знаки вопроса, закройте `nano` через `Ctrl+X` и проверьте кодировку сессии:

```bash
locale
```

В строке `LANG` должно быть что-то вроде `en_US.UTF-8` или `ru_RU.UTF-8`. Если там `C` или `POSIX`, включите UTF-8, выйдите из SSH и зайдите снова:

```bash
sudo apt update
sudo apt install -y locales
sudo locale-gen en_US.UTF-8
sudo update-locale LANG=en_US.UTF-8
```

После повторного входа снова откройте файл командой `sudo nano message.json` и вставьте текст ещё раз.

Необязательное поле `parse_mode` (`HTML` или `Markdown`) включает разметку Telegram. Тогда файл выглядит так:

```json
{
  "text": "Текст напоминания",
  "parse_mode": "HTML"
}
```

Снова `Ctrl+O`, `Enter`, `Ctrl+X`.

Запуск:

```bash
sudo docker compose pull
sudo docker compose up -d
```

Проверка:

```bash
sudo docker compose ps
sudo docker compose logs -f
```

В логе при старте есть часовой пояс и время следующего слота. Выход из логов: `Ctrl+C`, контейнер при этом продолжает работать.

## Как он шлёт сообщения

В каждом часе из `SEND_HOURS` сообщение уходит один раз. Если отправка не удалась, попытки повторяются до конца этого часа. Если контейнер в этом часе уже успешно отправил сообщение и потом перезапустился, повторной отправки нет: слот записан в том `reminder-data`. Если контейнер был выключен весь час, этот слот пропускается.

## Обновление

Новая версия кода попадает в реестр после зелёной сборки в `main`. На сервере достаточно скачать новый образ и пересоздать контейнер:

```bash
cd /opt/telegram-reminder
sudo docker compose pull
sudo docker compose up -d
```

Текст в `message.json` бот читает заново при каждой отправке. Чтобы сменить его:

```bash
cd /opt/telegram-reminder
sudo nano message.json
```

Поправьте текст, сохраните через `Ctrl+O`, `Enter` и выйдите через `Ctrl+X`. Перезапуск контейнера для этого не нужен.

После правки `.env` контейнер нужно пересоздать. Откройте файл, сохраните и выйдите так же, как при создании:

```bash
cd /opt/telegram-reminder
sudo nano .env
sudo docker compose up -d --force-recreate
```

## Остановка

Остановить контейнер и оставить историю слотов:

```bash
cd /opt/telegram-reminder
sudo docker compose down
```

Том `reminder-data` при этом сохраняется. Следующий `sudo docker compose up -d` продолжит с теми же слотами.
