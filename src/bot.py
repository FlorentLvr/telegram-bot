import os
import time
import asyncio
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Environment variables ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]  # e.g., https://your-service.onrender.com
ABI_API_URL = "https://abi-api.default.space.naas.ai/agents/Support/stream-completion"
ABI_API_TOKEN = os.environ["ABI_API_TOKEN"]  # Render environment variable

# --- Telegram bot handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! I’m your Support Assistant. Tell me any issue or feedback!"
    )

# Original streaming handler preserved
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    thread_id = str(update.message.chat_id)  # thread per chat

    payload = {"prompt": user_message, "thread_id": thread_id}
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
                    # SSE parsing
                    if raw_line.startswith("event:"):
                        event_name = raw_line[len("event:"):].strip()
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
                    # Update every 0.5s to avoid Telegram rate limits
                    if now - last_edit > 0.3:
                        try:
                            await reply_msg.edit_text(accumulated, parse_mode=ParseMode.MARKDOWN)
                        except Exception:
                            pass
                        last_edit = now

        # Final message after stream finishes
        if accumulated.strip():
            await reply_msg.edit_text(accumulated.strip(), parse_mode=ParseMode.MARKDOWN)
        else:
            await reply_msg.edit_text("✅ Got it — but no reply was returned.", parse_mode=ParseMode.MARKDOWN)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            await reply_msg.edit_text("⚠️ Unauthorized — check your API token.", parse_mode=ParseMode.MARKDOWN)
        else:
            await reply_msg.edit_text(f"❌ Error: {e.response.status_code} — {e.response.text}", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await reply_msg.edit_text(f"⚠️ Internal error: {e}", parse_mode=ParseMode.MARKDOWN)

# --- Build Telegram app ---
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", lambda u, c: u.message.reply_text("Just send me a message — I'll forward it to Support.")))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- Run webhook server (asyncio-friendly) ---
if __name__ == "__main__":
    # Automatically sets webhook and runs aiohttp server internally
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=f"{WEBHOOK_URL}/webhook"
    )
