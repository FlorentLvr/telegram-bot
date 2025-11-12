import os
import requests
from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

response = requests.post(
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
    data={
        "url": f"{WEBHOOK_URL}/webhook",
    }
)
print(response.status_code)
print(response.json())
