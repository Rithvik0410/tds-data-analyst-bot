"""
Per-run JSONL logging, pushed to the bot's own public GitHub repo so the
log is reachable at a stable, wget-able raw.githubusercontent.com URL.

Each run (one incoming grading question -> one final reply) gets its own
file at logs/<run_id>.jsonl in the repo. This avoids any read-modify-write
race between concurrent runs.
"""
import os
import json
import time
import uuid
import base64
import requests

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]          # "user/repo"
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

API_ROOT = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
RAW_ROOT = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"

_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


class RunLogger:
    """Buffers JSONL lines locally, then commits the whole file to GitHub
    once the run is complete (one commit per run keeps this cheap and
    avoids partial/garbled logs if something crashes mid-run)."""

    def __init__(self, chat_id: int):
        self.run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        self.chat_id = chat_id
        self.path = f"logs/{self.run_id}.jsonl"
        self._lines = []
        self.log("run_started", chat_id=chat_id)

    def log(self, event: str, **fields):
        entry = {"ts": time.time(), "event": event, **fields}
        self._lines.append(json.dumps(entry, default=str, ensure_ascii=False))

    def finalize_and_push(self) -> str:
        """Commits the accumulated JSONL to GitHub and returns the public
        raw URL. Falls back to returning the raw URL even if the push
        fails validation on retry, after a couple of attempts."""
        content = "\n".join(self._lines) + "\n"
        b64content = base64.b64encode(content.encode("utf-8")).decode("ascii")

        url = f"{API_ROOT}/{self.path}"
        payload = {
            "message": f"log: run {self.run_id} (chat {self.chat_id})",
            "content": b64content,
            "branch": GITHUB_BRANCH,
        }

        last_err = None
        for attempt in range(3):
            try:
                resp = requests.put(url, headers=_HEADERS, json=payload, timeout=20)
                if resp.status_code in (200, 201):
                    return f"{RAW_ROOT}/{self.path}"
                last_err = f"{resp.status_code}: {resp.text[:300]}"
            except requests.RequestException as e:
                last_err = str(e)
            time.sleep(1.5 * (attempt + 1))

        # Even on failure, return the URL we intended -- it may become
        # valid on a manual retry, and we don't want to crash the whole
        # reply just because logging failed.
        print(f"[logger] WARNING: failed to push log after retries: {last_err}")
        return f"{RAW_ROOT}/{self.path}"
