import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
MESSAGE = os.getenv("MESSAGE", "").strip()


def main() -> None:
    if not TOKEN or not CHAT_ID or not MESSAGE:
        print("Заполните BOT_TOKEN, CHAT_ID и MESSAGE в файле .env")
        sys.exit(1)

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": MESSAGE},
        timeout=30,
    )
    data = response.json()

    if not data.get("ok"):
        print("Ошибка Telegram:", data)
        sys.exit(1)

    print("Сообщение отправлено")


if __name__ == "__main__":
    main()