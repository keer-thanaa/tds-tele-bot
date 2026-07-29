import json
import time
import os
import base64
import requests
from openai import OpenAI
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

# --- these come from environment variables, never hardcode them ---
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
AIPIPE_TOKEN = os.environ["AIPIPE_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]          # NEW: needs 'repo' scope

LOG_URL = "https://raw.githubusercontent.com/keer-thanaa/tds-tele-bot/main/run.jsonl"

GITHUB_OWNER = "keer-thanaa"
GITHUB_REPO = "tds-tele-bot"
GITHUB_PATH = "run.jsonl"          # path of the file in the repo
GITHUB_BRANCH = "main"
# --------------------------------------------------------------------

client = OpenAI(base_url="https://aipipe.org/openai/v1", api_key=AIPIPE_TOKEN)
LOG_FILE = "run.jsonl"

# Keeps the last few messages per chat, so multi-turn questions work.
conversation_history = {}


def push_log_to_github():
    """Push the local run.jsonl file to GitHub so LOG_URL always reflects the latest logs."""
    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_PATH}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    with open(LOG_FILE, "r") as f:
        content = f.read()
    encoded_content = base64.b64encode(content.encode()).decode()

    # Get the current file's SHA (required for updates; None if file doesn't exist yet)
    resp = requests.get(api_url, headers=headers, params={"ref": GITHUB_BRANCH})
    sha = resp.json().get("sha") if resp.status_code == 200 else None

    payload = {
        "message": "Update run.jsonl log",
        "content": encoded_content,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload)
    if put_resp.status_code not in (200, 201):
        print(f"[WARN] Failed to push log to GitHub: {put_resp.status_code} {put_resp.text}")


def log_event(event: dict):
    event["timestamp"] = time.time()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")
    push_log_to_github()   # NEW: push after every log line


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = update.message.text
    log_event({"type": "incoming", "chat_id": chat_id, "text": user_text})

    history = conversation_history.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    system_prompt = (
        "You are a careful data analyst. The user's LAST message asks a data-analysis "
        "question and tells you exactly what JSON shape to reply with. Work out the "
        "real answer (use any public data you know, e.g. MOSPI statistics, general "
        "world knowledge, or arithmetic on numbers given in the message). "
        "Reply with ONLY that exact JSON object and absolutely nothing else - no "
        "explanation, no markdown, no code fences, just the raw JSON."
    )

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[{"role": "system", "content": system_prompt}] + history[-6:],
    )

    reply_text = response.choices[0].message.content.strip()
    history.append({"role": "assistant", "content": reply_text})

    try:
        parsed = json.loads(reply_text)
    except json.JSONDecodeError:
        start, end = reply_text.find("{"), reply_text.rfind("}")
        parsed = json.loads(reply_text[start:end + 1])

    parsed["log_url"] = LOG_URL
    final_reply = json.dumps(parsed)

    log_event({"type": "outgoing", "chat_id": chat_id, "text": final_reply})
    await update.message.reply_text(final_reply)


app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
print("Bot is running... (Ctrl+C to stop)")
app.run_polling()
