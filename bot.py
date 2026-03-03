import httpx
import os
import re
import logging
import time
import sys
from enum import Enum
from urllib.parse import urlparse
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# from config import TELEGRAM_BOT_TOKEN, OLLAMA_URL, OLLAMA_MODEL
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OLLAMA_URL = os.getenv("OLLAMA_URL")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")
URL_REGEX = r"(https?://[^\s]+|[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})"

## LOGGING CONFIG
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("telegram-ollama-bot")

class Intent(str, Enum):
    SKIP = "skip"
    STATUS = "status"
    LLM = "llm"

## Functions

## URL Validator
def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return all([parsed.scheme, parsed.netloc])

def extract_url(text: str):
    match = re.search(URL_REGEX, text)
    if not match:
        return None
    url = match.group(1)

    # If no scheme, add https automatically
    if not url.startswith("http"):
        url = f"https://{url}"

    return url

## HELP COMMAND
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 *Homelab Assistant*

I support multiple tools and natural language routing.

━━━━━━━━━━━━━━━━━━

🧠 *LLM Assistant*
Ask technical or general questions.

Natural:
• `Can you explain what a CPU is?`
• `How does Kubernetes networking work?`

Explicit:
• `!llm explain docker volumes`
• `ask llm summarize terraform`

━━━━━━━━━━━━━━━━━━

🔎 *Service Status Checks*
Check if a website is reachable.

Command:
• `/status https://example.com`

Natural:
• `is example.com up?`
• `check status of grafana.homelab.xyz`
• `why is app.homelab.xyz down?`

━━━━━━━━━━━━━━━━━━

⏭ *Skip / Ignore Tool*
Log a message without triggering any tool.

• `!skip this is just a debug note`

The bot will react with 👍 and ignore processing.

━━━━━━━━━━━━━━━━━━

📚 *Commands*

• `/help` — show this help message  
• `/status <url>` — explicit status check  

💡 Tip: You can speak naturally.  
I automatically detect whether to run a status check or use the LLM.
"""

    await update.message.reply_text(
        help_text,
        parse_mode="Markdown"
    )

## SYSTEM PROMPT
SYSTEM_PROMPT = """
You are an AI Specialist for a homelab environment.

Style:
- Helpful and clear.
- Technical but friendly.
- Detailed when useful, but avoid overly long answers.
- Prefer short paragraphs.
- Use bullets for steps.
- Keep responses suitable for Telegram chat.
- Default to concise unless the user asks for deep detail.
"""

## OLLAMA CALL
async def ask_ollama(prompt: str) -> str:
    start = time.time()
    logger.info(f"ollama_request | model={OLLAMA_MODEL}")

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(OLLAMA_URL, json=payload)
            response.raise_for_status()
            data = response.json()

        duration = round(time.time() - start, 2)

        logger.info(
            f"ollama_response | duration={duration}s"
        )

        return data.get("response", "No response from model.")

    except Exception as e:
        logger.error(f"ollama_failed | error={str(e)}")
        raise

## TEXT RESPONSES (if markdown fails)
async def safe_reply(update, text):
    """
    Try markdown first.
    If Telegram parsing fails, fallback to plain text.
    """
    try:
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

    except BadRequest as e:
        logger.warning(f"markdown_failed | fallback_to_text | error={e}")

        await update.message.reply_text(
            text,
            disable_web_page_preview=True
        )

## MESSAGE HANDLER
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    logger.info(
        f"message_received | user={user.username} "
        f"id={user.id} chat={chat_id} text='{text}'"
    )

    intent = detect_intent(text)

    logger.info(f"intent_detected | intent={intent}")

    if intent == Intent.SKIP:
        await handle_skip(update, text)
        return

    if intent == Intent.STATUS:
        await handle_status(update, text)
        return

    # Default → LLM
    await handle_llm(update, text)

## STATUS CHECKER
async def check_url_status(url: str):
    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(url)

        latency = round((time.time() - start) * 1000, 2)

        return {
            "up": 200 <= r.status_code < 400,
            "status_code": r.status_code,
            "latency": latency,
            "error": None,
        }

    except Exception as e:
        return {
            "up": False,
            "status_code": None,
            "latency": None,
            "error": str(e),
        }

def detect_intent(text: str) -> Intent:
    text_lower = text.lower()

    # Skip tool
    if text_lower.startswith("!skip"):
        return Intent.SKIP

    # Status tool
    if is_status_request(text_lower):
        return Intent.STATUS

    # Everything else defaults to LLM
    return Intent.LLM

def is_status_request(text: str) -> bool:
    text_lower = text.lower()

    url = extract_url(text_lower)
    if not url:
        return False

    status_keywords = [
        "status",
        "up",
        "down",
        "online",
        "offline",
        "alive",
        "health",
        "reachable",
        "running",
        "working",
    ]

    return any(word in text_lower for word in status_keywords)

## SKIP TOOL
async def handle_skip(update: Update, text: str):
    logger.info(f"skip_message | text='{text}'")

    # React with 👍
    try:
        await update.message.set_reaction("👍")
    except Exception:
        # Fallback if reactions not supported
        await update.message.reply_text("👍")

    return

## HANDLE STATUS
async def handle_status(update: Update, text: str):
    url = extract_url(text)

    if not url:
        await update.message.reply_text("🔎 I couldn't find a URL to check.")
        return

    await update.message.reply_text("🔎 Checking status...")

    result = await check_url_status(url)

    if result["up"]:
        msg = (
            f"🟢 *Service UP*\n\n"
            f"URL: `{url}`\n"
            f"Status: `{result['status_code']}`\n"
            f"Latency: `{result['latency']} ms`"
        )
    else:
        msg = (
            f"🔴 *Service DOWN*\n\n"
            f"URL: `{url}`\n"
            f"Error: `{result['error']}`"
        )

    await safe_reply(update, msg)

## HANDLE LLM
async def handle_llm(update: Update, text: str):
    trigger_prefixes = ["!llm", "ask llm"]

    trigger_used = next(
        (p for p in trigger_prefixes if text.lower().startswith(p)),
        None
    )

    if trigger_used:
        prompt = text[len(trigger_used):].strip()
    else:
        prompt = text

    if not prompt:
        await update.message.reply_text("Please provide a prompt.")
        return

    processing_msg = await update.message.reply_text(
        f"🧠 Thinking using {OLLAMA_MODEL}..."
    )

    try:
        reply = await ask_ollama(prompt)

        try:
            await processing_msg.edit_text(
                reply,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except BadRequest:
            await processing_msg.edit_text(reply)

    except Exception:
        logger.exception("llm_failed")
        await processing_msg.edit_text("⚠️ Error talking to Ollama")

## STATUS CHECKER: TELEGRAM COMMAND
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage:\n/status https://app.homelab.xyz"
        )
        return

    url = context.args[0]
    if 'http' not in url.split('://')[0]:
        url = 'https://' + url
        logger.info(f'adjusted url to {url}')
    else:
        logger.info(f'url untouched {url}')

    if not is_valid_url(url):
        await update.message.reply_text("❌ Invalid URL")
        return

    await update.message.reply_text("🔎 Checking status...")

    result = await check_url_status(url)

    if result["up"]:
        msg = (
            f"🟢 *Service UP*\n\n"
            f"URL: `{url}`\n"
            f"Status: `{result['status_code']}`\n"
            f"Latency: `{result['latency']} ms`"
        )
    else:
        msg = (
            f"🔴 *Service DOWN*\n\n"
            f"URL: `{url}`\n"
            f"Error: `{result['error']}`"
        )

    await safe_reply(update, msg)

## MAIN
def main():
    logger.info("Starting telegram bot...")
    logger.info(f"Using Ollama: {OLLAMA_URL}")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("status", status_command))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()

