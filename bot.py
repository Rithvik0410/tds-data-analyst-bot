"""
Telegram long-polling loop.

Long polling (getUpdates) is used instead of webhooks so this can run on a
plain VM with no public HTTPS endpoint/cert needed -- only outbound HTTPS
to api.telegram.org, Groq, GitHub, and target data sites.
"""
import os
import json
import time
import traceback
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

from agent import answer_question          # noqa: E402  (needs env vars loaded first)
from logger import RunLogger                # noqa: E402

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Per-chat rolling message history, for multi-turn questions.
# Kept small and in-memory -- fine for a single-process bot; if you need
# persistence across restarts, swap this for a small sqlite table.
HISTORY = defaultdict(list)
MAX_HISTORY_TURNS = 10


def get_updates(offset=None, timeout=50):
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json()["result"]


def send_message(chat_id, text):
    # No parse_mode -- we want the raw JSON string sent verbatim, with no
    # Markdown/HTML escaping that could corrupt braces or quotes.
    resp = requests.post(
        f"{API_BASE}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def handle_message(chat_id: int, text: str):
    rlog = RunLogger(chat_id)
    try:
        HISTORY[chat_id].append({"role": "user", "content": text})
        HISTORY[chat_id] = HISTORY[chat_id][-MAX_HISTORY_TURNS:]

        rlog.log("incoming_message", text=text, history_len=len(HISTORY[chat_id]))

        answer_value = answer_question(HISTORY[chat_id], rlog)

        log_url = rlog.finalize_and_push()
        reply_obj = {"answer": answer_value, "log_url": log_url}
        reply_text = json.dumps(reply_obj, ensure_ascii=False)

        if len(reply_text) > 4096:
            # Telegram hard cap -- log it, and fail loudly rather than
            # silently truncating the JSON into something invalid.
            rlog.log("reply_too_long", length=len(reply_text))
            reply_text = json.dumps(
                {"answer": None, "log_url": log_url, "error": "answer too long"},
                ensure_ascii=False,
            )

        send_message(chat_id, reply_text)
        rlog.log("reply_sent", reply=reply_text)

    except Exception as e:  # noqa: BLE001 - never let one bad question kill the loop
        rlog.log("fatal_error", error=str(e), traceback=traceback.format_exc())
        log_url = rlog.finalize_and_push()
        try:
            send_message(
                chat_id,
                json.dumps({"answer": None, "log_url": log_url, "error": str(e)}),
            )
        except Exception:
            pass


def main():
    print("Bot started, polling for updates...")
    offset = None
    while True:
        try:
            updates = get_updates(offset)
        except requests.RequestException as e:
            print(f"[poll] transient error: {e}, retrying in 5s")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message or "text" not in message:
                continue
            chat_id = message["chat"]["id"]
            text = message["text"]
            print(f"[recv] chat={chat_id} text={text[:120]!r}")
            handle_message(chat_id, text)


if __name__ == "__main__":
    main()
