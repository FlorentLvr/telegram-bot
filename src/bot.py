import os
import time
import httpx
import logging
import threading
import asyncio
import tempfile
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
from openai import OpenAI

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
WEBHOOK_URL_DEV = "https://unsuppressed-observable-arlette.ngrok-free.dev"
if os.environ.get("ENV") == "dev":
    WEBHOOK_URL = WEBHOOK_URL_DEV
else:
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", WEBHOOK_URL_DEV)
ABI_API_URL = "https://abi-api.default.space.naas.ai/agents/Support/completion"
ABI_API_TOKEN = os.environ["ABI_API_TOKEN"]
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai_client: OpenAI | None = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    logger.warning("OPENAI_API_KEY not set - voice transcription will not work")

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

# --- Voice message handling functions ---
async def download_voice_file(voice, bot_token, save_dir=None):
    """Download voice file from Telegram and return the file path."""
    if not voice or not hasattr(voice, 'file_id'):
        logger.error("No voice message found")
        return None
    
    file_id = voice.file_id
    
    # Step 1: Get file_path for the voice file using async httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.telegram.org/bot{bot_token}/getFile",
            params={"file_id": file_id}
        )
        resp.raise_for_status()
        file_info = resp.json()
        if not file_info.get("ok") or "file_path" not in file_info.get("result", {}):
            logger.error("Failed to get file_path from Telegram API")
            return None
        
        file_path = file_info["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        
        # Step 2: Download the file
        response = await client.get(download_url)
        response.raise_for_status()
        
        # Step 3: Save the file temporarily
        if save_dir is None:
            save_dir = tempfile.gettempdir()
        os.makedirs(save_dir, exist_ok=True)
        unique_id = getattr(voice, 'file_unique_id', file_id)
        ogg_file = os.path.join(save_dir, f"{unique_id}.ogg")
        
        with open(ogg_file, "wb") as f:
            f.write(response.content)
        
        logger.info(f"Voice file downloaded to: {ogg_file}")
        return ogg_file

async def transcribe_audio_with_openai(audio_file_path):
    """Transcribe audio file using OpenAI. Supports OGG and other formats."""
    if not openai_client:
        raise RuntimeError("OPENAI_API_KEY not set - cannot transcribe audio")
    
    # Run the synchronous OpenAI call in an executor to avoid blocking the event loop
    def _transcribe():
        with open(audio_file_path, "rb") as audio_file:
            transcription = openai_client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio_file
            )
        return transcription.text
    
    loop = asyncio.get_event_loop()
    transcribed_text = await loop.run_in_executor(None, _transcribe)
    logger.info(f"Transcription: {transcribed_text}")
    return transcribed_text

# --- Shared function to send message to ABI API ---
async def send_to_abi_api(user_message, thread_id, reply_msg):
    """Send user message to ABI API and update the reply message."""
    payload = {"prompt": user_message, "thread_id": thread_id}
    headers = {
        "Authorization": f"Bearer {ABI_API_TOKEN}",
        # "Accept": "text/event-stream",  # Streaming - commented for completion endpoint
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
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

# --- Non-streaming message handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    thread_id = str(update.message.chat_id)
    logger.info(f"Received message from {thread_id}: {user_message}")

    reply_msg = await update.message.reply_text("⏳ Thinking...", parse_mode=ParseMode.MARKDOWN)
    await send_to_abi_api(user_message, thread_id, reply_msg)

# --- Voice message handler ---
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages by downloading, transcribing, and processing them."""
    voice = update.message.voice
    thread_id = str(update.message.chat_id)
    logger.info(f"Received voice message from {thread_id}")
    
    if not openai_client:
        await update.message.reply_text(
            "❌ Voice transcription is not available. Please set OPENAI_API_KEY environment variable.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    reply_msg = await update.message.reply_text("🎤 Transcribing voice...", parse_mode=ParseMode.MARKDOWN)
    
    try:
        # Download the voice file
        ogg_file = await download_voice_file(voice, BOT_TOKEN)
        if not ogg_file:
            await reply_msg.edit_text("❌ Failed to download voice file", parse_mode=ParseMode.MARKDOWN)
            return
        
        # Transcribe the audio
        await reply_msg.edit_text("📝 Processing transcription...", parse_mode=ParseMode.MARKDOWN)
        transcribed_text = await transcribe_audio_with_openai(ogg_file)
        
        # Clean up the temporary file
        try:
            os.remove(ogg_file)
        except Exception as e:
            logger.warning(f"Failed to delete temporary file {ogg_file}: {e}")
        
        if not transcribed_text or not transcribed_text.strip():
            await reply_msg.edit_text("❌ Could not transcribe the voice message", parse_mode=ParseMode.MARKDOWN)
            return
        
        logger.info(f"Transcribed text from {thread_id}: {transcribed_text}")
        
        # Send the transcribed text to ABI API
        await reply_msg.edit_text(f"⏳ Responding to: {transcribed_text}...", parse_mode=ParseMode.MARKDOWN)
        await send_to_abi_api(transcribed_text, thread_id, reply_msg)
        
    except Exception as e:
        error_msg = f"⚠️ Error processing voice message: {e}"
        await reply_msg.edit_text(error_msg, parse_mode=ParseMode.MARKDOWN)
        logger.error(f"Error processing voice message for {thread_id}: {e}")

# --- Add handlers ---
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))

# --- Flask app for webhook ---
flask_app = Flask(__name__)

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update_json = request.get_json(force=True)
    logger.info(f"Webhook message received: {update_json}")
    update = Update.de_json(update_json, app.bot)
    
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
