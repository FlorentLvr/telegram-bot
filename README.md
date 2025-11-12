# Telegram Bot to AI Agent

## Prerequisites

* Telegram account (mobile or desktop)
* Git & GitHub account
* Visual Studio Code or Cursor (IDE)
* ngrok account (for local webhook testing)
* Render account (for production)
* OpenAI account (to transcribe voice messages)
* Your AI agent token / API endpoint

# 1. Create the bot on Telegram (BotFather)

1. Open Telegram and search for **@BotFather**.
2. Start a chat and send:

   ```
   /newbot
   ```
3. Follow prompts:

   * Choose a **name** (display name).
   * Choose a **username** that ends with `bot` (e.g. `my_support_bot`).
4. BotFather responds with your **Bot Token** (format like `123456:ABC...`).
   **Save it** — you will store it in environment variables (never commit to Git).

# 2. Setup Repository

1. Clone this repository:

   ```bash
   git clone https://github.com/your-username/your-bot-repo.git
   cd your-bot-repo
   ```

2. Copy the example environment file and update values:

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and fill in your:
   - `BOT_TOKEN`
   - `OPENAI_API_KEY`
   - `ABI_API_TOKEN`
   - `WEBHOOK_URL`

3. Install dependencies (ideally in a virtual environment):

   ```bash
   pip install -r src/requirements.txt
   ```


# 3. Local webhook testing with ngrok

Telegram webhooks require a public HTTPS URL — ngrok can expose your local server.

1. Start ngrok:

```bash
ngrok http 10000
```

2. Copy HTTPS URL from ngrok (e.g. `https://abc123.ngrok.io`) and in `bot.py` in variable: `WEBHOOK_URL_DEV` 


3. Start your bot (so it registers or uses new `WEBHOOK_URL`).

```bash
python bot.py
```

4. Manually set webhook (optional — your code may set it):

```bash
curl -X POST "https://api.telegram.org/bot$BOT_TOKEN/setWebhook?url=$WEBHOOK_URL/webhook"
```

5. Verify:

```bash
curl "https://api.telegram.org/bot$BOT_TOKEN/getWebhookInfo"
```

Look for `"url": "https://abc123.ngrok.io/webhook"` and no recent 404 errors.

6. Send a message in Telegram and watch:

* Bot terminal logs (SSE chunks and edits)
* ngrok request inspector ([http://127.0.0.1:4040](http://127.0.0.1:4040)) shows incoming POSTs


# 6. Render deployment (production)

1. Create a new **Web Service** on Render and connect your GitHub repo.
2. Settings:
   * Environment: Python 3
   * Branch: main
   * Root folder: src
   * Build Command: `pip install -r requirements.txt`
   * Start Command: `python bot.py` (ensure bot runs Flask and background PTB thread)
3. Environment variables on Render:
   * `BOT_TOKEN` = your Telegram token
   * `WEBHOOK_URL` = `https://<your-render-service>.onrender.com` (no trailing `/webhook`)
   * `ABI_API_TOKEN` = your AI API token
   * `OPENAI_API_KEY` = your OPENAI_API_KEY
4. Optionally add `render.yaml` to describe service.
5. Deploy — check Render logs:
   * Look for startup logs, `Webhook URL` log, `Telegram Application started`, and streaming logs.