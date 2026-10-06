import html
import logging
import os
import tempfile
from pathlib import Path

from openai import AsyncOpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = "".join(os.getenv("TELEGRAM_BOT_TOKEN", "").split())
OPENAI_API_KEY = "".join(os.getenv("OPENAI_API_KEY", "").split())
TRANSCRIPTION_MODEL = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")
TRANSCRIPTION_LANGUAGE = os.getenv("OPENAI_TRANSCRIPTION_LANGUAGE", "ru")
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")

client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SUPPORTED_EXTENSIONS = {
    ".flac", ".mp3", ".mp4", ".mpeg", ".mpga",
    ".m4a", ".ogg", ".wav", ".webm",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Racooon is awake.\n\n"
        "Send me a voice message, audio, video, or an audio/video file.\n"
        "I’ll turn it into text and sort out what matters.\n\n"
        "/start — start Racooon\n"
        "/help — help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Пришли голосовое, аудио или видео.\n\n"
        "Я расшифрую его, разложу по смыслу и сохраню оригинальный текст."
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


async def send_long_text(message, text: str, parse_mode=None):
    chunk_size = 3900
    for i in range(0, len(text), chunk_size):
        await message.reply_text(
            text[i:i + chunk_size],
            parse_mode=parse_mode,
        )


async def structure_transcript(transcript: str) -> str:
    response = await client.responses.create(
        model=TEXT_MODEL,
        instructions=(
            "Ты — Racooon. Превращай расшифровку голосового сообщения в очень короткую "
            "и полезную структуру. Пиши на том же языке, что и пользователь. "
            "Не выдумывай факты, даты, время, имена или задачи. "
            "Сначала пойми смысл временных связей, и только потом сокращай. "
            "Не привязывай время автоматически к ближайшему глаголу. Различай: "
            "(1) когда выполнить действие, (2) дедлайн/к какому моменту результат должен быть готов, "
            "(3) когда произойдёт другое событие. "
            "Если человек говорит 'упакуй посылку, я приду за ней через два часа', это НЕ значит "
            "'через два часа упаковать посылку'. Это значит, что посылка должна быть упакована к моменту "
            "прихода через два часа. В таком случае пиши, например: "
            "'Упаковать посылку → готово к приходу через 2 часа'. "
            "Если в речи есть дата или время, показывай их рядом именно с тем событием или дедлайном, "
            "к которому они относятся. "
            "Каждую отдельную задачу или мысль пиши с новой строки через символ →. "
            "Если одна дата/время относится к нескольким задачам, укажи дату/время только один раз. "
            "Не добавляй заголовок, вступление, слово 'Оригинал' или пояснения. "
            "Пример входа: 'Завтра в четыре позвонить Маше, заказать торт и забрать посылку.' "
            "Пример выхода:\n"
            "Завтра, 16:00 → позвонить Маше\n"
            "→ заказать торт\n"
            "→ забрать посылку"
        ),
        input=transcript,
    )
    return (response.output_text or "").strip()


async def make_notes(transcript: str) -> str:
    response = await client.responses.create(
        model=TEXT_MODEL,
        instructions=(
            "Ты — Racooon. Сделай полезные заметки по расшифровке, а не просто пересказ. "
            "Пиши на том же языке, что и пользователь. Ничего не выдумывай и не достраивай. "
            "Убирай повторы, слова-паразиты и лишние формулировки. "
            "Разложи информацию только по тем разделам, для которых реально есть данные. "
            "Допустимые разделы: "
            "📌 Задачи, ⏰ Даты и сроки, 👤 Люди, 💡 Идеи и факты, 📍 Места, 🔗 Ссылки и контакты. "
            "Не показывай пустые разделы. "
            "Внутри разделов используй короткие пункты с символом •. "
            "Смысл временных связей сохраняй точно: отличай время действия от дедлайна и от времени другого события. "
            "Если из фразы следует, что что-то должно быть готово к определённому моменту, так и пиши — не переноси это время на само действие."
        ),
        input=transcript,
    )
    return (response.output_text or "").strip()


def format_structured_text(structured: str) -> str:
    lines = [line.strip() for line in structured.splitlines() if line.strip()]
    if not lines:
        return ""

    rendered = []
    first = lines[0]
    if "→" in first:
        left, right = first.split("→", 1)
        rendered.append(
            f"<b>{html.escape(left.strip())}</b> → {html.escape(right.strip())}"
        )
    else:
        rendered.append(f"<b>{html.escape(first)}</b>")

    for line in lines[1:]:
        rendered.append(html.escape(line))

    return "\n".join(rendered)


def remember_transcript(context: ContextTypes.DEFAULT_TYPE, item_id: str, transcript: str):
    items = context.user_data.setdefault("racooon_items", {})
    items[item_id] = {"transcript": transcript}

    # Keep only a small recent in-memory history for the MVP.
    if len(items) > 20:
        oldest_key = next(iter(items))
        items.pop(oldest_key, None)


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
                    language=TRANSCRIPTION_LANGUAGE,
                    prompt="Русская разговорная речь. Транскрибируй дословно, не переводи.",
                )

        original = (transcript.text or "").strip()
        if not original:
            await status.edit_text(
                "🦝 Я всё прослушал, но не смог уверенно распознать речь."
            )
            return

        try:
            structured = await structure_transcript(original)
        except Exception:
            logger.exception("Structuring failed")
            structured = f"→ {original}"

        item_id = str(message.message_id)
        remember_transcript(context, item_id, original)

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("Полный текст", callback_data=f"full:{item_id}"),
                InlineKeyboardButton("Сделать заметки", callback_data=f"notes:{item_id}"),
            ]]
        )

        body = format_structured_text(structured)
        await status.edit_text(
            "🦝 <b>Енот всё разложил.</b>\n\n" + body,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception("Transcription failed")
        await status.edit_text(
            "🦝 Что-то пошло не так при разборе файла. "
            "Попробуй ещё раз чуть позже."
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        action, item_id = query.data.split(":", 1)
    except ValueError:
        return

    item = context.user_data.get("racooon_items", {}).get(item_id)
    if not item:
        await query.message.reply_text(
            "🦝 Этот текст уже не лежит у меня под лапой. Пришли файл ещё раз."
        )
        return

    original = item["transcript"]

    if action == "full":
        await send_long_text(
            query.message,
            "<b>Оригинал:</b>\n" + html.escape(original),
            parse_mode="HTML",
        )
        return

    if action == "notes":
        status = await query.message.reply_text("🦝 Енот делает заметки…")
        try:
            notes = await make_notes(original)
            if not notes:
                notes = original
            await status.edit_text(
                "📝 <b>Заметки</b>\n\n" + html.escape(notes),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Notes failed")
            await status.edit_text(
                "🦝 Не получилось сделать заметки. Попробуй ещё раз чуть позже."
            )


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 Пришли мне голосовое, аудио или видео — я разберу его."
    )


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_callback))
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
