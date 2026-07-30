"""
One-time setup: tells Telegram to POST updates to your deployed webhook
URL instead of you having to long-poll. Run this once after your Render
service is live (and again any time the URL changes).

Usage:
    python set_webhook.py https://your-app.onrender.com
"""
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

if len(sys.argv) != 2:
    print("Usage: python set_webhook.py https://your-app.onrender.com")
    sys.exit(1)

base_url = sys.argv[1].rstrip("/")
webhook_url = f"{base_url}/webhook/{BOT_TOKEN}"

resp = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
    json={"url": webhook_url},
    timeout=20,
)
print(resp.status_code, resp.json())

# Sanity check: ask Telegram what it currently has on file.
info = requests.get(
    f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo", timeout=20
)
print(info.json())
