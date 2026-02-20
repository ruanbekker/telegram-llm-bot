import httpx
import os
import logging
import time
import sys
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

#from config import TELEGRAM_BOT_TOKEN, OLLAMA_URL, OLLAMA_MODEL
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OLLAMA_URL = os.getenv("OLLAMA_URL")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")

## LOGGING CONFIG
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("telegram-ollama-bot")

## HELP COMMAND
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 *Homelab LLM Bot*

Trigger the LLM using:

• `!llm your question`
• `ask llm your question`

Examples:
`!llm explain kubernetes like I'm 5`
`ask llm summarize docker compose`

Commands:
/help - show this help message
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


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

    trigger_prefixes = ["!llm", "ask llm"]

    trigger_used = None
    for prefix in trigger_prefixes:
        if text.lower().startswith(prefix):
            trigger_used = prefix
            break

    if not trigger_used:
        # Ignore non-trigger messages
        return

    prompt = text[len(trigger_used):].strip()

    if not prompt:
        await update.message.reply_text("Please provide a prompt after the trigger.")
        return

    # Show typing indicator
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    try:
        reply = await ask_ollama(prompt)

        # Preserve formatting
        # await update.message.reply_text(
        #    reply,
        #    parse_mode=ParseMode.MARKDOWN,
        #    disable_web_page_preview=True
        # )

        # Use safe responses function
        await safe_reply(update, reply)

    except Exception as e:
        logger.exception("message_handling_failed")
        await update.message.reply_text(f"⚠️ Error talking to Ollama:\n{e}")

## MAIN
def main():
    logger.info("Starting telegram bot...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()

