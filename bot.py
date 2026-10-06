import logging
import os
import tempfile
from pathlib import Path

from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = "".join(os.getenv("TELEGRAM_BOT_TOKEN", "").split())
OPENAI_API_KEY = "".join(os.getenv("OPENAI_API_KEY", "").split())
TRANSCRIPTION_MODEL = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")

client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SUPPORTED_EXTENSIONS = {
    ".flac", ".mp3", ".mp4", ".mpeg", ".mpga",
    ".m4a", ".ogg", ".wav", ".webm",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Racooon is awake.\n\n"
        "Send me a voice message, audio, video, or an audio/video file.\n"
        "I’ll turn it into text.\n\n"
        "/start — start Racooon\n"
        "/help — help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Send me a voice message, audio, video, or a supported audio/video file.\n\n"
        "I’ll transcribe it and send the text back here."
    )


def get_media_info(message):
    if message.voice:
        return message.voice.file_id, "voice.ogg"

    if message.audio:
        filename = message.audio.file_name or "audio.mp3"
        return message.audio.file_id, filename

    if message.video:
        filename = message.video.file_name or "video.mp4"
        return message.video.file_id, filename

    if message.document:
        filename = message.document.file_name or "file"
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            return None, None
        return message.document.file_id, filename

    return None, None


async def send_long_text(message, text: str):
    chunk_size = 3900
    for i in range(0, len(text), chunk_size):
        await message.reply_text(text[i:i + chunk_size])


async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    if not client:
        await message.reply_text(
            "🦝 Енот нашёл файл, но у него пока нет ключа к транскрипции."
        )
        return

    file_id, filename = get_media_info(message)
    if not file_id:
        await message.reply_text(
            "🦝 Пока я умею разбирать аудио и видео. "
            "Для обычных документов модуль появится позже."
        )
        return

    status = await message.reply_text("🦝 Енот уже разбирается…")

    try:
        telegram_file = await context.bot.get_file(file_id)

        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            suffix = ".ogg"

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / f"racooon_upload{suffix}"
            await telegram_file.download_to_drive(custom_path=local_path)

            if local_path.stat().st_size > 25 * 1024 * 1024:
                await status.edit_text(
                    "🦝 Файл больше 25 МБ. Пока отправь, пожалуйста, файл поменьше."
                )
                return

            with local_path.open("rb") as audio_file:
                transcript = await client.audio.transcriptions.create(
                    model=TRANSCRIPTION_MODEL,
                    file=audio_file,
                )

        text = (transcript.text or "").strip()
        if not text:
            await status.edit_text(
                "🦝 Я всё прослушал, но не смог уверенно распознать речь."
            )
            return

        await status.edit_text("🦝 Готово. Енот всё разложил.")
        await send_long_text(message, text)

    except Exception:
        logger.exception("Transcription failed")
        await status.edit_text(
            "🦝 Что-то пошло не так при разборе файла. "
            "Попробуй ещё раз чуть позже."
        )


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Пришли мне голосовое, аудио или видео — я превращу его в текст."
    )


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO | filters.VIDEO | filters.Document.ALL,
            receive_file,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
