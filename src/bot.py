import os
import asyncio
import uuid
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

# Handle user messages → forward to AI agent
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.message.chat_id
    thread_id = str(user_id)  # use chat_id as thread_id, keeps continuity per user

    try:
        # Build payload
        payload = {
            "prompt": user_message,
            "thread_id": thread_id
        }

        headers = {
            "Authorization": f"Bearer {ABI_API_TOKEN}",
            "accept": "application/json",
            "Content-Type": "application/json"
        }

        # Send request to your AI Support Agent
        response = requests.post(ABI_API_URL, json=payload, headers=headers, params={"token": ABI_API_TOKEN})

        if response.status_code == 200:
            ai_reply = response.text or "✅ Got it — but no reply was returned."
        elif response.status_code == 401:
            ai_reply = "⚠️ Unauthorized — please check your API token."
        else:
            ai_reply = f"❌ Error: {response.status_code} — {response.text}"

        await update.message.reply_text(ai_reply, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Internal error: {e}")

# --- Add handlers ---
tg_app.add_handler(CommandHandler("start", start))
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
