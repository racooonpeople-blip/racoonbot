import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Racooon is awake.\n\n"
        "Send me an audio, voice message, video, or document.\n"
        "Transcription is the next module we're adding.\n\n"
        "/start — start Racooon\n/help — help"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 RacooonBot\n\n"
        "Right now I can receive your files. "
        "Next we'll connect transcription and formatting."
    )

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Got it. I can see the file.\n\n"
        "The transcription engine will be connected next."
    )

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Send me an audio, voice message, video, or document."
    )

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.VIDEO | filters.Document.ALL,
        receive_file
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
