import os
import time
import asyncio
import httpx
import logging
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
    level=logging.INFO,  # change to logging.DEBUG to see every SSE line
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Environment variables ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = "https://unsuppressed-observable-arlette.ngrok-free.dev"  # placeholder
ABI_API_URL = "https://abi-api.default.space.naas.ai/agents/Support/stream-completion"
ABI_API_TOKEN = os.environ["ABI_API_TOKEN"]

# --- Telegram command handlers ---
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

# --- Streaming handler with logging ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    thread_id = str(update.message.chat_id)

    logger.info(f"Received message from {thread_id}: {user_message}")

    payload = {"prompt": user_message, "thread_id": thread_id}
    headers = {
        "Authorization": f"Bearer {ABI_API_TOKEN}",
        "Accept": "text/event-stream",
    }

    reply_msg = await update.message.reply_text("⏳ Thinking...", parse_mode=ParseMode.MARKDOWN)
    accumulated = ""
    last_edit = time.monotonic()
    last_sent_text = ""  # Track last text sent to avoid "Message not modified"
    event_name = None

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", ABI_API_URL, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    logger.debug(f"Raw SSE line: {raw_line}")

                    # SSE parsing
                    if raw_line.startswith("event:"):
                        event_name = raw_line[len("event:"):].strip()
                        logger.debug(f"Detected event: {event_name}")
                        continue
                    if not raw_line.startswith("data:"):
                        continue
                    if event_name != "message":
                        continue

                    chunk = raw_line[len("data:"):].strip()
                    if not chunk or chunk == "[DONE]":
                        continue

                    accumulated += chunk
                    now = time.monotonic()

                    # Edit message only if text changed
                    if now - last_edit > 0.5 and accumulated != last_sent_text:
                        try:
                            await reply_msg.edit_text(accumulated, parse_mode=ParseMode.MARKDOWN)
                            last_sent_text = accumulated
                            logger.info(f"Updated message for {thread_id}: {accumulated}")
                        except Exception as e:
                            logger.error(f"Failed to edit message: {e}")
                        last_edit = now

        # Final message after streaming finishes
        if accumulated.strip() and accumulated != last_sent_text:
            await reply_msg.edit_text(accumulated.strip(), parse_mode=ParseMode.MARKDOWN)
            logger.info(f"Final message sent to {thread_id}: {accumulated.strip()}")
        elif not accumulated.strip():
            await reply_msg.edit_text("✅ Got it — but no reply was returned.", parse_mode=ParseMode.MARKDOWN)
            logger.info(f"No reply returned for {thread_id}")

    except httpx.HTTPStatusError as e:
        error_text = f"HTTP error {e.response.status_code}: {e.response.text}"
        await reply_msg.edit_text(f"❌ {error_text}", parse_mode=ParseMode.MARKDOWN)
        logger.error(error_text)
    except Exception as e:
        await reply_msg.edit_text(f"⚠️ Internal error: {e}", parse_mode=ParseMode.MARKDOWN)
        logger.error(f"Internal error for {thread_id}: {e}")

# --- Build Telegram app ---
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- Run polling for local testing / easier debugging ---
if __name__ == "__main__":
    logger.info("Bot is starting in polling mode...")
    app.run_polling()
