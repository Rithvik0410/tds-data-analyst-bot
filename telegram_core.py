"""
Shared Telegram bot logic, independent of how updates arrive (long-polling
or webhook). Both bot.py (polling entrypoint, for VM-style hosting) and
app.py (webhook entrypoint, for Render-style hosting) import from here.
"""
import os
import json
import traceback
from collections import defaultdict

import requests

from agent import answer_question
from logger import RunLogger

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Per-chat rolling message history, for multi-turn questions.
# Kept small and in-memory -- fine for a single-process bot; if you need
# persistence across restarts/multiple instances, swap this for a small
# sqlite table or an external store.
HISTORY = defaultdict(list)
MAX_HISTORY_TURNS = 10


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

    except Exception as e:  # noqa: BLE001 - never let one bad question kill the process
        rlog.log("fatal_error", error=str(e), traceback=traceback.format_exc())
        log_url = rlog.finalize_and_push()
        try:
            send_message(
                chat_id,
                json.dumps({"answer": None, "log_url": log_url, "error": str(e)}),
            )
        except Exception:
            pass
