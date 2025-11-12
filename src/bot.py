import os
import time
import httpx
import logging
import threading
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.constants import ParseMode

# --- Load .env if available ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,  # DEBUG for SSE streaming details
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Environment variables ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = "https://unsuppressed-observable-arlette.ngrok-free.dev"
ABI_API_URL = "https://abi-api.default.space.naas.ai/agents/Support/completion"
ABI_API_TOKEN = os.environ["ABI_API_TOKEN"]

# --- Telegram application ---
app = ApplicationBuilder().token(BOT_TOKEN).build()

# Global variable to store the event loop for webhook processing
application_event_loop = None

# --- Command handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I’m your Support Assistant. Tell me any issue or feedback!"
    )
    logger.info(f"User {update.message.chat_id} started the bot")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just send me a message — I'll forward it to Support."
    )
    logger.info(f"User {update.message.chat_id} requested help")

# --- Non-streaming message handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    thread_id = str(update.message.chat_id)
    logger.info(f"Received message from {thread_id}: {user_message}")

    payload = {"prompt": user_message, "thread_id": thread_id}
    headers = {
        "Authorization": f"Bearer {ABI_API_TOKEN}",
        # "Accept": "text/event-stream",  # Streaming - commented for completion endpoint
    }

    reply_msg = await update.message.reply_text("⏳ Thinking...", parse_mode=ParseMode.MARKDOWN)

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            # For completion (non-streaming), use the regular completion endpoint
            resp = await client.post(ABI_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.info(f"Completion response: {data}")
            # Expecting that the response json contains the reply text under 'text'
            # Adjust key as needed depending on endpoint output format
            reply_text = str(data)
            
            if reply_text and reply_text.strip():
                await reply_msg.edit_text(reply_text.strip(), parse_mode=ParseMode.MARKDOWN)
                logger.info(f"Final message sent to {thread_id}: {reply_text.strip()}")
            else:
                await reply_msg.edit_text("✅ Got it — but no reply was returned.", parse_mode=ParseMode.MARKDOWN)
                logger.info(f"No reply returned for {thread_id}")

    except httpx.HTTPStatusError as e:
        error_text = f"HTTP error {e.response.status_code}: {e.response.text}"
        await reply_msg.edit_text(f"❌ {error_text}", parse_mode=ParseMode.MARKDOWN)
        logger.error(error_text)
    except Exception as e:
        await reply_msg.edit_text(f"⚠️ Internal error: {e}", parse_mode=ParseMode.MARKDOWN)
        logger.error(f"Internal error for {thread_id}: {e}")

# --- Add handlers ---
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- Flask app for webhook ---
flask_app = Flask(__name__)

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update_json = request.get_json(force=True)
    update = Update.de_json(update_json, app.bot)
    logger.info(f"Webhook message received: {update_json}")
    
    # Process update directly using the application's event loop
    global application_event_loop
    if application_event_loop and application_event_loop.is_running():
        try:
            asyncio.run_coroutine_threadsafe(
                app.process_update(update),
                application_event_loop
            )
        except Exception as e:
            logger.error(f"Error processing update: {e}")
            # Fallback: put in queue if processing fails
            try:
                app.update_queue.put_nowait(update)
            except Exception as e2:
                logger.error(f"Failed to queue update: {e2}")
    else:
        # Fallback: put in queue if event loop not ready
        try:
            app.update_queue.put_nowait(update)
            logger.info("Update queued (event loop not ready)")
        except Exception as e:
            logger.error(f"Failed to queue update: {e}")
    
    return "ok", 200

@flask_app.route("/")
def home():
    return "Bot is alive 🚀", 200

# --- Start Telegram Application in background thread ---
async def start_application():
    await app.initialize()
    await app.start()
    logger.info("Telegram Application started (handlers active)")

def run_async_loop():
    global application_event_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    application_event_loop = loop
    loop.run_until_complete(start_application())
    # Keep the loop running to process updates
    loop.run_forever()

# --- Run Flask server ---
if __name__ == "__main__":
    import sys
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Starting Flask webhook server on port {port}")
    logger.info(f"Webhook URL: {WEBHOOK_URL}/webhook")

    # Start Telegram app in background
    threading.Thread(target=run_async_loop, daemon=True).start()
    
    # Give the app time to initialize
    time.sleep(2)

    try:
        flask_app.run(host="0.0.0.0", port=port)
    except Exception as e:
        logger.error(f"Failed to start Flask server: {e}")
        sys.exit(1)
