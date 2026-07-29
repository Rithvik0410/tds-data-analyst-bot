# tds-data-analyst-bot

A Telegram bot backed by an LLM agent that answers data-analysis questions
(MOSPI and similar public datasets) and replies with a single JSON object.

Everything here uses **free-tier services only**: Groq (LLM), DuckDuckGo
(search, no key needed), and your own GitHub repo (log hosting). Hosting is
meant to run on Oracle Cloud's Always-Free VM (genuinely free forever, not a
trial), but any always-on Linux box works the same way.

---

## 1. What this bot does

1. Long-polls Telegram for new messages sent by the grading account.
2. Keeps a per-chat message history (for multi-turn questions).
3. Runs a ReAct-style agent loop with three tools:
   - `web_search` — DuckDuckGo HTML search, no API key.
   - `web_fetch` — downloads a URL (HTML/CSV/etc.) and returns cleaned text.
   - `python_exec` — runs Python (pandas/numpy available) in a subprocess to
     do real computation instead of the LLM guessing numbers.
4. Logs every step (tool calls, inputs/outputs, final answer) as JSONL,
   appends it to `logs/<run_id>.jsonl` in this repo, and computes the
   `raw.githubusercontent.com` URL for that file.
5. Sends back **exactly one JSON object** as the Telegram reply — the code
   builds this JSON itself (not the raw LLM text) to guarantee valid,
   minimal output: `{"answer": ..., "log_url": "..."}`.

---

## 2. Get your free API keys / tokens

### 2.1 Telegram bot token
1. Open Telegram, message `@BotFather`.
2. `/newbot` → choose a display name → choose a **username ending in `bot`**
   (e.g. `my_data_analyst_bot`).
3. Copy the token it gives you (`123456:ABC-...`).

### 2.2 Groq API key (free LLM with tool calling)
1. Go to https://console.groq.com → sign up (no card required).
2. Create an API key under "API Keys".
3. Free tier gives generous daily/per-minute limits — enough for this task.

### 2.3 GitHub token (for pushing logs to this repo)
1. Push this code to a **public** GitHub repo (required — grading needs a
   public repo URL and public log URLs).
2. GitHub → Settings → Developer settings → Personal access tokens →
   fine-grained token, scoped to just this repo, with **Contents: read and
   write** permission.
3. Copy the token.

---

## 3. Configure

Copy `.env.example` to `.env` and fill in:

```
TELEGRAM_BOT_TOKEN=...
GROQ_API_KEY=...
GITHUB_TOKEN=...
GITHUB_REPO=yourusername/tds-data-analyst-bot
GITHUB_BRANCH=main
```

---

## 4. Run locally first

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
python bot.py
```

Message your bot on Telegram with a test question. You should get back a
single-line JSON reply within a minute or two.

### Test against the official grading harness

```bash
git clone https://github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot
cd tds-p1-t2-2026-telegram-bot
# point its config at your bot's username per its own README
# add sample questions to evals/questions.json
# run the harness
```

Add a handful of your own MOSPI-style questions to `evals/questions.json`
in *this* repo too (see the example file) — this mirrors the shape the
harness expects, so you can sanity-check locally.

---

## 5. Deploy for free, permanently (Oracle Cloud Always Free VM)

Oracle's "Always Free" tier includes an ARM Ampere A1 VM (up to 4 OCPU /
24GB RAM) that never expires and never gets billed as long as you stay
within the free shape. This is the recommended host because it runs
indefinitely with no sleep/cold-start, unlike most free PaaS tiers.

### 5.1 Create the VM
1. Sign up at https://www.oracle.com/cloud/free/ (a card is required for
   identity verification only; the Always Free shapes are not charged).
2. Create a Compute Instance:
   - Shape: **VM.Standard.A1.Flex** (Ampere, Always Free eligible)
   - Image: **Ubuntu 24.04**
   - Add your SSH key
3. Note the public IP. Open outbound internet (default egress is fine —
   long polling only needs outbound HTTPS to `api.telegram.org`, Groq,
   GitHub, and target data sites, no inbound port needed).

### 5.2 Install Docker
```bash
ssh ubuntu@<VM_IP>
sudo apt update && sudo apt install -y docker.io git
sudo systemctl enable --now docker
```

### 5.3 Clone and run
```bash
git clone https://github.com/<you>/tds-data-analyst-bot.git
cd tds-data-analyst-bot
cp .env.example .env   # fill in real values
sudo docker build -t tds-bot .
sudo docker run -d --name tds-bot --restart=always --env-file .env tds-bot
```

`--restart=always` means Docker restarts the bot automatically after VM
reboots or crashes — this is what keeps it "reachable during grading"
without you babysitting it.

### 5.4 Verify it's alive
```bash
sudo docker logs -f tds-bot
```
You should see `Bot started, polling for updates...`. Send it a test
message from Telegram.

### 5.5 Optional: external watchdog
Register the VM's uptime with a free monitor like UptimeRobot pinging
nothing directly useful here (no HTTP port), so instead just rely on
`restart=always` plus, if you want extra safety, a cron job:

```bash
# crontab -e
*/10 * * * * docker inspect -f '{{.State.Running}}' tds-bot | grep -q true || docker start tds-bot
```

---

## 6. Repo layout

```
bot.py                 # Telegram long-polling loop
agent.py                # LLM agent loop (Groq, tool calling)
logger.py               # JSONL run logging + push to GitHub
tools/web.py             # web_search (DuckDuckGo) + web_fetch
tools/python_exec.py     # sandboxed python execution tool
requirements.txt
Dockerfile
.env.example
evals/questions.json     # your own test questions
```

---

## 7. Known limitations / things to tune

- DuckDuckGo HTML scraping can occasionally rate-limit under heavy use —
  there's a small retry/backoff built in.
- `python_exec` runs in a subprocess with a timeout (default 30s) and no
  network access inside the exec itself — it's for computation on data
  you've already fetched, not for fetching directly.
- The agent loop caps at `MAX_TOOL_STEPS` (default 8) to avoid infinite
  loops burning your Groq quota; tune in `agent.py` if questions need more
  research steps.
- Telegram messages are capped at 4096 chars — keep answers compact
  (the JSON formatter will fail loudly if you exceed this, rather than
  silently truncating).
