import os
import asyncio
import requests
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode
from dotenv import load_dotenv

load_dotenv()

# --- Flask for webhook endpoint ---
app_flask = Flask(__name__)

# --- Environment variables ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
ABI_API_URL = "https://abi-api.default.space.naas.ai/agents/Support/stream-completion"
ABI_API_TOKEN = os.environ["ABI_API_TOKEN"]  # store your API token in Render environment variables

# --- Telegram bot setup ---
tg_app = ApplicationBuilder().token(BOT_TOKEN).build()

# Simple start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hi! I’m your Support Assistant. Tell me any issue or feedback!")

# Handle user messages → forward to AI agent (streamed, as in abi_stream_completion)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import httpx
    import time

    user_message = update.message.text
    thread_id = str(update.message.chat_id)  # use chat_id as thread_id, keeps continuity per user

    payload = {
        "prompt": user_message,
        "thread_id": thread_id
    }
    headers = {
        "Authorization": f"Bearer {ABI_API_TOKEN}",
        "Accept": "text/event-stream",
    }

    reply_msg = await update.message.reply_text("⏳ Thinking...", parse_mode=ParseMode.MARKDOWN)
    accumulated = ""
    last_edit = time.monotonic()
    event_name = None

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", ABI_API_URL, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    # Parse SSE 'event: ...' lines
                    if raw_line.startswith('event:'):
                        event_name = raw_line[len("event:"):].strip()
                        continue
                    # Only process data lines for event=='message'
                    if not raw_line.startswith("data:"):
                        continue
                    if event_name != "message":
                        continue
                    chunk = raw_line[len("data:"):].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    accumulated += chunk
                    now = time.monotonic()
                    # Edit the previous message every 0.5s with the current accumulated text
                    if now - last_edit > 0.5:
                        # In Telegram, editing very frequently can result in errors/rate-limiting, so throttle
                        try:
                            await reply_msg.edit_text(accumulated, parse_mode=ParseMode.MARKDOWN)
                        except Exception:
                            pass
                        last_edit = now

        # Final update after streaming is complete
        if accumulated.strip():
            await reply_msg.edit_text(accumulated.strip(), parse_mode=ParseMode.MARKDOWN)
        else:
            await reply_msg.edit_text("✅ Got it — but no reply was returned.", parse_mode=ParseMode.MARKDOWN)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            await reply_msg.edit_text("⚠️ Unauthorized — please check your API token.", parse_mode=ParseMode.MARKDOWN)
        else:
            await reply_msg.edit_text(f"❌ Error: {e.response.status_code} — {e.response.text}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await reply_msg.edit_text(f"⚠️ Internal error: {e}", parse_mode=ParseMode.MARKDOWN)

# --- Add handlers ---
tg_app.add_handler(
    CommandHandler("start", start)
)
tg_app.add_handler(
    CommandHandler("help", lambda u, c: u.message.reply_text("Just send me a message — I'll forward it to Support."))
)
tg_app.add_handler(
    # Handle all text messages (excluding commands)
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
)

# --- Flask routes for webhook ---
@app_flask.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), tg_app.bot)
    tg_app.update_queue.put_nowait(update)
    return "ok", 200

@app_flask.route("/")
def home():
    return "Bot is alive 🚀", 200

# --- Start up: set webhook ---
if __name__ == "__main__":
    async def set_webhook():
        await tg_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

    asyncio.run(set_webhook())
    app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
