"""
Webhook entrypoint, for hosting on Render's free tier (no credit card
required). Render's free web services sleep after ~15 min of inactivity;
Telegram's webhook POST wakes the service on the next incoming message,
and an external keep-alive ping (see README) can prevent sleep entirely
during a grading window.

Run with gunicorn in production (see Procfile):
    gunicorn app:app --workers 1 --threads 4 --timeout 120

--workers 1: keeps in-memory chat HISTORY consistent across requests.
--timeout 120: agent runs (web search + LLM + code exec) can take a while;
avoid gunicorn killing the worker mid-run.
"""
import os
import threading

from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

from telegram_core import handle_message  # noqa: E402

app = Flask(__name__)

# Used as a path secret so randos can't POST fake Telegram updates at your
# webhook -- only someone who knows your bot token can hit the real path.
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"


@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    # Hit by Render's health checks and by your external keep-alive pinger.
    return jsonify({"status": "ok"})


@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message")

    if message and "text" in message:
        chat_id = message["chat"]["id"]
        text = message["text"]
        print(f"[recv] chat={chat_id} text={text[:120]!r}")
        # Answering can take a while (web search + LLM + code exec).
        # Respond to Telegram immediately so it doesn't retry the webhook
        # delivery, and do the real work in a background thread.
        threading.Thread(target=handle_message, args=(chat_id, text), daemon=True).start()

    return jsonify({"ok": True})


if __name__ == "__main__":
    # Local testing only -- Render runs this via gunicorn (see Procfile).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
