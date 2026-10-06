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
TRANSCRIPTION_LANGUAGE = os.getenv("OPENAI_TRANSCRIPTION_LANGUAGE", "").strip()
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")

client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SUPPORTED_EXTENSIONS = {
    ".flac", ".mp3", ".mp4", ".mpeg", ".mpga",
    ".m4a", ".ogg", ".wav", ".webm",
}

TRANSCRIPTION_LANGUAGES = {
    "auto": ("Авто", None),
    "ru": ("Русский", "ru"),
    "en": ("English", "en"),
    "he": ("עברית", "he"),
    "ar": ("العربية", "ar"),
    "fa": ("فارسی", "fa"),
}


def language_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Авто", callback_data="lang:auto"),
                InlineKeyboardButton("Русский", callback_data="lang:ru"),
                InlineKeyboardButton("English", callback_data="lang:en"),
            ],
            [
                InlineKeyboardButton("עברית", callback_data="lang:he"),
                InlineKeyboardButton("العربية", callback_data="lang:ar"),
                InlineKeyboardButton("فارسی", callback_data="lang:fa"),
            ],
        ]
    )


def current_transcription_language(context: ContextTypes.DEFAULT_TYPE):
    selected = context.user_data.get("transcription_language", "auto")
    return TRANSCRIPTION_LANGUAGES.get(selected, TRANSCRIPTION_LANGUAGES["auto"])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    label, _ = current_transcription_language(context)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"🎙 Язык аудио: {label}", callback_data="language_menu")]]
    )
    await update.message.reply_text(
        "🦝 Racooon is awake.\n\n"
        "Пришли голосовое, аудио или видео — я превращу его в текст и разложу по смыслу.\n\n"
        "По умолчанию язык определяется автоматически.",
        reply_markup=keyboard,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    label, _ = current_transcription_language(context)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"🎙 Язык аудио: {label}", callback_data="language_menu")]]
    )
    await update.message.reply_text(
        "🦝 Пришли голосовое, аудио или видео.\n\n"
        "Я расшифрую его, разложу по смыслу и сохраню оригинальный текст.",
        reply_markup=keyboard,
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
            "и полезную структуру. Пиши на языке исходной речи; если в записи несколько языков, "
            "сохраняй их как в оригинале и не переводи без запроса пользователя. "
            "Не выдумывай факты, даты, время, имена или задачи. "
            "Сначала определи, есть ли вообще что структурировать. "
            "Если это короткая фраза, приветствие, вопрос, комментарий или сообщение без задач/сроков/фактов, "
            "верни только аккуратно очищенный смысл исходной фразы без рубрик, без советов и без фраз "
            "вроде 'необходимо уточнить', 'недостаточно информации' или 'нужно составить структуру'. "
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


async def translate_text(transcript: str, target_language: str) -> str:
    language_names = {
        "en": "English",
        "he": "Hebrew",
        "ru": "Russian",
        "ar": "Arabic",
        "fa": "Persian",
    }
    target = language_names.get(target_language)
    if not target:
        raise ValueError("Unsupported target language")

    response = await client.responses.create(
        model=TEXT_MODEL,
        instructions=(
            f"Translate the user's transcript into {target}. "
            "Preserve the meaning, names, numbers, dates, times, links, and paragraph structure. "
            "Do not summarize, explain, add commentary, or omit details. "
            "Return only the translation."
        ),
        input=transcript,
    )
    return (response.output_text or "").strip()


async def make_notes(transcript: str) -> str:
    response = await client.responses.create(
        model=TEXT_MODEL,
        instructions=(
            "Ты — Racooon. Твоя задача — ИЗВЛЕЧЬ заметки только из того, что прямо содержится в расшифровке. "
            "Не дополняй текст здравым смыслом, типичными действиями, советами, предположениями или шаблонными формулировками. "
            "Каждый пункт должен быть прямым фактом, задачей или деталью из исходного текста, только короче и чище. "
            "Если пункт нельзя подтвердить конкретной фразой из расшифровки — не пиши его. "
            "Пиши на том же языке, что и пользователь. Убирай повторы, слова-паразиты и разговорный шум. "

            "Используй разделы только если в исходнике действительно есть несколько разных типов содержательной информации. "
            "Допустимые разделы: 📌 Задачи, ⏰ Даты и сроки, 👤 Люди, 💡 Идеи и факты, 📍 Места, 🔗 Ссылки и контакты. "
            "Показывай только непустые разделы. Никогда не создавай раздел ради заполнения шаблона. "
            "Никогда не пиши 'нет данных', 'не указано', 'уточнить', 'связаться с коллегами' и подобные заглушки, если этого нет в исходнике. "

            "Строгие правила по разделам: "
            "📌 Задачи — только явно сказанные действия, просьбы, поручения или обязательства. "
            "⏰ Даты и сроки — только явно названные дата, время, период или дедлайн. Не придумывай новый срок. "
            "👤 Люди — только явно упомянутые имена, роли или конкретные люди. Не добавляй 'команда', 'коллеги', 'клиент', если их не называли. "
            "💡 Идеи и факты — только явно высказанные мысли, решения, факты или выводы из исходника. "
            "📍 Места — только явно названные места. Не придумывай офис, конференц-зал, дом и т.п. "
            "🔗 Ссылки и контакты — только если в исходнике реально есть URL, email, телефон, @username или иной конкретный контакт. "
            "Если таких данных нет, этого раздела быть не должно. "

            "Если исходный текст короткий или содержит одну-две мысли, вообще не используй разделы: дай 1–3 коротких пункта без заголовков. "
            "Если содержательных фактов почти нет, верни только то немногое, что реально сказано, без попытки сделать заметку 'богаче'. "
            "Смысл временных связей сохраняй точно: различай время действия, дедлайн и время другого события. "
            "Не меняй причинно-следственные связи и не превращай пожелание или общее рассуждение в задачу."
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
                _, selected_language = current_transcription_language(context)
                transcription_kwargs = {
                    "model": TRANSCRIPTION_MODEL,
                    "file": audio_file,
                    "prompt": (
                        "Transcribe exactly in the language or languages spoken. "
                        "Do not translate. Preserve names, numbers, dates, times, and code-switching."
                    ),
                }

                # Per-user choice wins. If Auto is selected, let the model detect the language.
                if selected_language:
                    transcription_kwargs["language"] = selected_language
                elif TRANSCRIPTION_LANGUAGE:
                    transcription_kwargs["language"] = TRANSCRIPTION_LANGUAGE

                transcript = await client.audio.transcriptions.create(
                    **transcription_kwargs
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
            [
                [
                    InlineKeyboardButton("Оригинал", callback_data=f"full:{item_id}"),
                    InlineKeyboardButton("Сделать заметки", callback_data=f"notes:{item_id}"),
                ],
                [
                    InlineKeyboardButton("Перевести", callback_data=f"translate:{item_id}"),
                ],
            ]
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

    parts = query.data.split(":")
    action = parts[0]

    if action == "language_menu" and len(parts) == 1:
        item_id = None
        target_language = None
    elif action == "tr" and len(parts) == 3:
        _, target_language, item_id = parts
    elif len(parts) == 2:
        action, item_id = parts
        target_language = None
    else:
        return

    if action == "language_menu":
        await query.message.reply_text(
            "🎙 Выбери язык, на котором говорят в аудио.\n\n"
            "Авто — Енот сам определит язык записи. Это не перевод.",
            reply_markup=language_keyboard(),
        )
        return

    if action == "lang":
        selected = item_id
        if selected not in TRANSCRIPTION_LANGUAGES:
            return
        context.user_data["transcription_language"] = selected
        label, _ = TRANSCRIPTION_LANGUAGES[selected]
        await query.message.reply_text(
            f"🦝 Язык аудио: <b>{html.escape(label)}</b>",
            parse_mode="HTML",
        )
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
        return

    if action == "translate":
        language_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("English", callback_data=f"tr:en:{item_id}"),
                    InlineKeyboardButton("עברית", callback_data=f"tr:he:{item_id}"),
                ],
                [
                    InlineKeyboardButton("Русский", callback_data=f"tr:ru:{item_id}"),
                    InlineKeyboardButton("العربية", callback_data=f"tr:ar:{item_id}"),
                    InlineKeyboardButton("فارسی", callback_data=f"tr:fa:{item_id}"),
                ],
            ]
        )
        await query.message.reply_text(
            "🌍 Куда перевести?",
            reply_markup=language_keyboard,
        )
        return

    if action == "tr":
        language_titles = {
            "en": "English",
            "he": "עברית",
            "ru": "Русский",
            "ar": "العربية",
            "fa": "فارسی",
        }
        title = language_titles.get(target_language, target_language)
        status = await query.message.reply_text(f"🦝 Перевожу → {title}…")
        try:
            translated = await translate_text(original, target_language)
            if not translated:
                translated = original
            await status.edit_text(
                f"🌍 <b>{html.escape(title)}</b>\n\n" + html.escape(translated),
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Translation failed")
            await status.edit_text(
                "🦝 Не получилось перевести. Попробуй ещё раз чуть позже."
            )


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    label, _ = current_transcription_language(context)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"🎙 Язык аудио: {label}", callback_data="language_menu")]]
    )
    await update.message.reply_text(
        "🦝 Пришли мне голосовое, аудио или видео — я разберу его.",
        reply_markup=keyboard,
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
