import html
import logging
import os
import tempfile
from time import monotonic

from security_guards import SlidingWindowLimiter
from pathlib import Path

import asyncpg
from openai import AsyncOpenAI
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
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
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# Set only after choosing a retention policy; 0 preserves existing recordings.
TRANSCRIPT_RETENTION_DAYS = max(0, int(os.getenv("TRANSCRIPT_RETENTION_DAYS", "0")))
AI_RATE_LIMITER = SlidingWindowLimiter(limit=12, window_seconds=600)
GLOBAL_AI_RATE_LIMITER = SlidingWindowLimiter(limit=80, window_seconds=60)
LAST_RETENTION_CHECK = 0.0

client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
db_pool = None

async def enforce_ai_rate_limit(update: Update) -> bool:
    """Limit paid AI calls per chat and globally within this process."""
    chat = update.effective_chat
    if chat and AI_RATE_LIMITER.allow(chat.id) and GLOBAL_AI_RATE_LIMITER.allow("global"):
        return True
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "🦝 Слишком много запросов за короткое время. Попробуй чуть позже."
        )
    return False


async def block_non_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Never process stored transcripts in a group or public channel."""
    if update.effective_chat and update.effective_chat.type != "private":
        raise ApplicationHandlerStop


SUPPORTED_EXTENSIONS = {
    ".flac", ".mp3", ".mp4", ".mpeg", ".mpga",
    ".m4a", ".ogg", ".wav", ".webm",
}

TRANSCRIPTION_LANGUAGES = {
    "auto": ("Не выбран", None),
    "ru": ("Русский", "ru"),
    "en": ("English", "en"),
    "he": ("עברית", "he"),
    "ar": ("العربية", "ar"),
    "fa": ("فارسی", "fa"),
}


def navigation_row(back_callback: str):
    return [
        InlineKeyboardButton("⬅️ Назад", callback_data=back_callback),
        InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu"),
    ]


def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📎 Загрузить файл / видео", callback_data="upload_media")],
            [InlineKeyboardButton("🔗 Вставить ссылку", callback_data="submit_link")],
            [InlineKeyboardButton("🎙 Выбрать язык аудио", callback_data="language_menu:main")],
            [InlineKeyboardButton("📚 Мои записи", callback_data="library")],
            [InlineKeyboardButton("Что там по монеткам? 🦝", callback_data="plans")],
        ]
    )


def language_keyboard(source: str = "main"):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Определить автоматически",
                    callback_data=f"lang:auto:{source}",
                ),
            ],
            [
                InlineKeyboardButton("Русский", callback_data=f"lang:ru:{source}"),
                InlineKeyboardButton("English", callback_data=f"lang:en:{source}"),
            ],
            [
                InlineKeyboardButton("עברית", callback_data=f"lang:he:{source}"),
                InlineKeyboardButton("العربية", callback_data=f"lang:ar:{source}"),
                InlineKeyboardButton("فارسی", callback_data=f"lang:fa:{source}"),
            ],
            navigation_row(
                f"result_menu:{source}" if source != "main" else "main_menu"
            ),
        ]
    )


def result_keyboard(item_id: str):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Оригинал", callback_data=f"full:{item_id}"),
                InlineKeyboardButton("Сделать заметки", callback_data=f"notes:{item_id}"),
            ],
            [
                InlineKeyboardButton("Перевести", callback_data=f"translate:{item_id}"),
            ],
            [
                InlineKeyboardButton(
                    "🎙 Выбрать язык аудио",
                    callback_data=f"language_menu:{item_id}",
                ),
            ],
            navigation_row("main_menu"),
        ]
    )


def translation_keyboard(item_id: str):
    return InlineKeyboardMarkup(
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
            navigation_row(f"result_menu:{item_id}"),
        ]
    )


def output_navigation_keyboard(item_id: str):
    return InlineKeyboardMarkup([navigation_row(f"result_menu:{item_id}")])


async def current_transcription_language(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
):
    selected = context.user_data.get("transcription_language")
    if selected in TRANSCRIPTION_LANGUAGES:
        return selected, TRANSCRIPTION_LANGUAGES[selected]

    if db_pool is not None:
        try:
            saved = await db_pool.fetchrow(
                """
                SELECT transcription_language, transcription_language_confirmed
                FROM user_settings
                WHERE chat_id = $1
                """,
                chat_id,
            )
            if (
                saved
                and saved["transcription_language_confirmed"]
                and saved["transcription_language"] in TRANSCRIPTION_LANGUAGES
            ):
                selected = saved["transcription_language"]
                context.user_data["transcription_language"] = selected
                context.user_data["transcription_language_confirmed"] = True
                return selected, TRANSCRIPTION_LANGUAGES[selected]
        except Exception:
            logger.exception("Loading language preference failed")

    selected = None
    return selected, ("Не выбран", None)


async def set_transcription_language(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    selected: str,
):
    if selected not in TRANSCRIPTION_LANGUAGES:
        raise ValueError("Unsupported transcription language")

    context.user_data["transcription_language"] = selected
    context.user_data["transcription_language_confirmed"] = True

    if db_pool is not None:
        await db_pool.execute(
            """
            INSERT INTO user_settings (
                chat_id,
                transcription_language,
                transcription_language_confirmed
            )
            VALUES ($1, $2, TRUE)
            ON CONFLICT (chat_id)
            DO UPDATE SET
                transcription_language = EXCLUDED.transcription_language,
                transcription_language_confirmed = TRUE,
                updated_at = NOW()
            """,
            chat_id,
            selected,
        )


async def init_db(application: Application):
    global db_pool

    if not DATABASE_URL:
        logger.warning("DATABASE_URL is not set; persistent storage is disabled")
        return

    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    await db_pool.execute(
        """
        CREATE TABLE IF NOT EXISTS transcripts (
            chat_id BIGINT NOT NULL,
            item_id BIGINT NOT NULL,
            original TEXT NOT NULL,
            structured TEXT,
            language_code TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_id, item_id)
        )
        """
    )
    await db_pool.execute(
        "ALTER TABLE transcripts ADD COLUMN IF NOT EXISTS language_code TEXT"
    )
    await db_pool.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            chat_id BIGINT PRIMARY KEY,
            transcription_language TEXT NOT NULL DEFAULT 'auto',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await db_pool.execute(
        """
        ALTER TABLE user_settings
        ADD COLUMN IF NOT EXISTS transcription_language_confirmed BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    await db_pool.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            chat_id BIGINT NOT NULL,
            item_id BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (chat_id, item_id)
        )
        """
    )
    try:
        await cleanup_expired_data(force=True)
    except Exception:
        logger.exception("Privacy cleanup failed during startup")

    logger.info("Persistent transcript storage is ready")

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Главное меню"),
            BotCommand("library", "Мои записи"),
            BotCommand("language", "Выбрать язык аудио"),
            BotCommand("help", "Что умеет Енот"),
        ]
    )
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands()
    )
    logger.info("Telegram command menu is ready")


async def cleanup_expired_data(force: bool = False):
    """Remove expired transcripts only when opted in; minimize old quota records."""
    global LAST_RETENTION_CHECK
    if db_pool is None:
        return
    now = monotonic()
    if not force and now - LAST_RETENTION_CHECK < 24 * 60 * 60:
        return
    if TRANSCRIPT_RETENTION_DAYS > 0:
        await db_pool.execute(
            "DELETE FROM transcripts WHERE created_at < "
            "NOW() - ($1::int * INTERVAL '1 day')",
            TRANSCRIPT_RETENTION_DAYS,
        )
    await db_pool.execute(
        "DELETE FROM usage_events WHERE created_at < "
        "date_trunc('month', NOW()) - INTERVAL '2 months'"
    )
    LAST_RETENTION_CHECK = now


async def close_db(application: Application):
    global db_pool
    if db_pool is not None:
        await db_pool.close()
        db_pool = None


async def save_transcript(
    chat_id: int,
    item_id: str,
    original: str,
    structured: str,
    language_code: str | None,
):
    if db_pool is None:
        return

    await db_pool.execute(
        """
        INSERT INTO transcripts (chat_id, item_id, original, structured, language_code)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (chat_id, item_id)
        DO UPDATE SET
            original = EXCLUDED.original,
            structured = EXCLUDED.structured,
            language_code = EXCLUDED.language_code
        """,
        chat_id,
        int(item_id),
        original,
        structured,
        language_code,
    )
    try:
        await cleanup_expired_data()
    except Exception:
        logger.exception("Privacy cleanup failed")


async def load_transcript(chat_id: int, item_id: str):
    if db_pool is None:
        return None

    return await db_pool.fetchrow(
        """
        SELECT original, structured, language_code
        FROM transcripts
        WHERE chat_id = $1 AND item_id = $2
        """,
        chat_id,
        int(item_id),
    )


async def load_recent_transcripts(chat_id: int, limit: int = 8):
    if db_pool is None:
        return []

    return await db_pool.fetch(
        """
        SELECT item_id, original, structured, language_code, created_at
        FROM transcripts
        WHERE chat_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        chat_id,
        limit,
    )


async def search_transcripts(chat_id: int, search_text: str, limit: int = 8):
    if db_pool is None:
        return []

    rows = await db_pool.fetch(
        """
        SELECT item_id, original, structured, language_code, created_at
        FROM transcripts
        WHERE chat_id = $1
        ORDER BY created_at DESC
        LIMIT 80
        """,
        chat_id,
    )
    if not rows:
        return []

    # Fast literal pass first. This keeps obvious searches instant and cheap.
    words = [word.casefold().strip(".,!?;:()[]{}«»\"'") for word in search_text.split()]
    words = [word for word in words if len(word) >= 2]
    scored = []
    for row in rows:
        haystack = f"{row['original']} {row['structured'] or ''}".casefold()
        score = sum(haystack.count(word) for word in words)
        if score:
            scored.append((score, row))

    if scored:
        scored.sort(key=lambda pair: (pair[0], pair[1]["created_at"]), reverse=True)
        return [row for _, row in scored[:limit]]

    # If the user remembers the meaning rather than the exact words, ask the model
    # to rank the saved records semantically. Only IDs from this user's rows are valid.
    candidates = []
    by_id = {}
    for row in rows:
        item_id = str(row["item_id"])
        by_id[item_id] = row
        text_value = (row["structured"] or row["original"] or "").strip()
        candidates.append(f"ID {item_id}: {text_value[:1200]}")

    try:
        response = await client.responses.create(
            model=TEXT_MODEL,
            input=(
                "You search a private user's saved transcript library. "
                "Return only the IDs of records that are genuinely relevant to the query, "
                "most relevant first, maximum 8. Do not invent IDs. "
                "If nothing is relevant, return NONE.\n\n"
                f"QUERY: {search_text}\n\n"
                "RECORDS:\n" + "\n\n".join(candidates)
            ),
        )
        raw = (response.output_text or "").strip()
        if raw.upper() == "NONE":
            return []

        import re
        found_ids = []
        for candidate_id in re.findall(r"\d+", raw):
            if candidate_id in by_id and candidate_id not in found_ids:
                found_ids.append(candidate_id)
            if len(found_ids) >= limit:
                break
        return [by_id[item_id] for item_id in found_ids]
    except Exception:
        logger.exception("Semantic library search failed")
        return []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>Приветствую тебя в RacooonBot! 🦝</b>\n\n"
        "<b>Вот чем я могу быть тебе полезен:</b>\n"
        "1. Расшифровать голосовое, аудио или видео в текст.\n"
        "2. Разобрать запись и выделить главное: задачи, даты, людей и важные детали.\n"
        "3. Сделать из записи понятные заметки.\n"
        "4. Перевести текст на другой язык.\n"
        "5. Сохранить всё в «Мои записи», чтобы ты мог найти это позже.\n\n"
        "<b>У тебя есть 4 способа передать мне материал:</b>\n"
        "1. 🎙 Отправить голосовое сообщение.\n"
        "2. 🎧 Загрузить аудиофайл.\n"
        "3. 🎬 Загрузить видео или файл.\n"
        "4. 🔗 Вставить ссылку на видео.\n\n"
        "<b>🎙 Язык аудио</b>\n"
        "Выбери язык, на котором будут говорить в записи.\n"
        "От выбранного языка напрямую зависит качество и точность распознавания текста.\n\n"
        "Выбор обязателен перед первой записью. Если язык неизвестен, нажми «Определить автоматически», но точность может быть ниже.\n\n"
        "<b>Имей в виду:</b> это не перевод.\n"
        "Перевод доступен после обработки записи в разделе «🌍 Перевести».",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦝 <b>Что умеет Енот</b>\n\n"
        "🎙 Расшифровывает голосовые и аудио\n"
        "🎬 Разбирает видео\n"
        "📝 Делает заметки и выделяет главное\n"
        "🌍 Переводит готовый текст\n"
        "📚 Сохраняет записи\n"
        "🔎 Ищет сохранённое по словам и по смыслу\n\n"
        "Перед первой записью выбери язык аудио. Если язык неизвестен — используй «Определить автоматически».",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


async def plans_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "🦝 <b>Что там по монеткам?</b>\n\n"
        "Выбери, сколько работы можно свалить на Енота:\n\n"
        "<b>🦝 Потестить Енота, но не кормить — 0 ₪</b>\n"
        "4 файла в месяц · до 5 минут каждый\n\n"
        "<b>🦝 Енот в кармане — 19 ₪/мес.</b>\n"
        "20 файлов в месяц · до 15 минут каждый\n\n"
        "<b>🦝 Енот на связи — 29 ₪/мес.</b>\n"
        "50 файлов в месяц · до 30 минут каждый\n\n"
        "<b>🦝 Енот на работе — 42 ₪/мес.</b>\n"
        "100 файлов в месяц · до 60 минут каждый",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🦝 Потестить Енота, но не кормить · 0 ₪", callback_data="plan:free")],
                [InlineKeyboardButton("🦝 Енот в кармане · 19 ₪", callback_data="plan:pocket")],
                [InlineKeyboardButton("🦝 Енот на связи · 29 ₪", callback_data="plan:connected")],
                [InlineKeyboardButton("🦝 Енот на работе · 42 ₪", callback_data="plan:work")],
                navigation_row("main_menu"),
            ]
        ),
    )


async def plan_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    plans = {
        "free": ("🦝 Потестить Енота, но не кормить", "0 ₪", "4 файла в месяц", "до 5 минут каждый"),
        "pocket": ("🦝 Енот в кармане", "19 ₪/мес.", "20 файлов в месяц", "до 15 минут каждый"),
        "connected": ("🦝 Енот на связи", "29 ₪/мес.", "50 файлов в месяц", "до 30 минут каждый"),
        "work": ("🦝 Енот на работе", "42 ₪/мес.", "100 файлов в месяц", "до 60 минут каждый"),
    }
    key = (query.data or "").split(":", 1)[1]
    plan = plans.get(key)
    if not plan:
        return

    name, price, files, duration = plan
    extra = (
        "\n\nЭтот план уже активен автоматически."
        if key == "free"
        else "\n\nОплату подключаем следующим шагом. Пока Енот покажет тебе план, но деньги не спишет."
    )
    await query.message.reply_text(
        f"<b>{name}</b>\n"
        f"<b>{price}</b>\n\n"
        f"{files}\n"
        f"{duration}{extra}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [navigation_row("plans")]
        ),
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, (label, _) = await current_transcription_language(
        context,
        update.effective_chat.id,
    )
    await update.message.reply_text(
        f"🎙 Сейчас: <b>{html.escape(label)}</b>\n\n"
        "Выбери язык, на котором говорят в аудио. "
        "От выбранного языка напрямую зависит качество распознавания и точность текста. "
        "Это не перевод.",
        parse_mode="HTML",
        reply_markup=language_keyboard("main"),
    )


async def library_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await load_recent_transcripts(update.effective_chat.id)

    if not rows:
        await update.message.reply_text(
            "📚 <b>Мои записи</b>\n\n"
            "Здесь пока пусто. Пришли Еноту первую запись — и она появится здесь.",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        return

    buttons = [
        [InlineKeyboardButton(library_item_title(row), callback_data=f"library_item:{row['item_id']}")]
        for row in rows
    ]
    buttons.append([InlineKeyboardButton("🔎 Найти в записях", callback_data="library_search")])
    buttons.append(navigation_row("main_menu"))

    await update.message.reply_text(
        "📚 <b>Мои записи</b>\n\n"
        "Последние сохранённые записи:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
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


async def send_long_text(message, text: str, parse_mode=None, reply_markup=None):
    chunk_size = 3900
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or [""]
    for index, chunk in enumerate(chunks):
        await message.reply_text(
            chunk,
            parse_mode=parse_mode,
            reply_markup=reply_markup if index == len(chunks) - 1 else None,
        )


async def structure_transcript(transcript: str, language_code: str | None = None) -> str:
    output_languages = {
        "ru": "Russian",
        "en": "English",
        "he": "Hebrew",
        "ar": "Arabic",
        "fa": "Persian",
    }
    forced_language = output_languages.get(language_code)
    language_rule = (
        f"Write the result strictly in {forced_language}. Do not switch to a related language. "
        if forced_language
        else "Write in exactly the language of the transcript. Do not switch to a related language. "
    )

    response = await client.responses.create(
        model=TEXT_MODEL,
        instructions=(
            language_rule
            + "Russian and Ukrainian are different languages: never translate or switch between them unless explicitly asked. "
            + "Ты — Racooon. Превращай расшифровку голосового сообщения в очень короткую "
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


async def make_notes(transcript: str, language_code: str | None = None) -> str:
    output_languages = {
        "ru": "Russian",
        "en": "English",
        "he": "Hebrew",
        "ar": "Arabic",
        "fa": "Persian",
    }
    forced_language = output_languages.get(language_code)
    language_rule = (
        f"Write the result strictly in {forced_language}. Do not switch to a related language. "
        if forced_language
        else "Write in exactly the language of the transcript. Do not switch to a related language. "
    )

    response = await client.responses.create(
        model=TEXT_MODEL,
        instructions=(
            language_rule
            + "Russian and Ukrainian are different languages: never translate or switch between them unless explicitly asked. "
            + "Ты — Racooon. Твоя задача — ИЗВЛЕЧЬ заметки только из того, что прямо содержится в расшифровке. "
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


def remember_transcript(
    context: ContextTypes.DEFAULT_TYPE,
    item_id: str,
    transcript: str,
    language_code: str | None = None,
):
    items = context.user_data.setdefault("racooon_items", {})
    items[item_id] = {
        "transcript": transcript,
        "language_code": language_code,
    }

    # Keep only a small recent in-memory history for the MVP.
    if len(items) > 20:
        oldest_key = next(iter(items))
        items.pop(oldest_key, None)


async def reserve_free_usage(chat_id: int, item_id: int):
    """Atomically reserve a free slot before paying for transcription."""
    if db_pool is None:
        raise RuntimeError("Quota database is unavailable")
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", chat_id)
            used = await conn.fetchval(
                """
                SELECT COUNT(*) FROM usage_events
                WHERE chat_id = $1
                  AND created_at >= date_trunc('month', NOW())
                  AND created_at < date_trunc('month', NOW()) + INTERVAL '1 month'
                """,
                chat_id,
            )
            if used >= 4:
                return False, used
            await conn.execute(
                "INSERT INTO usage_events (chat_id, item_id) VALUES ($1, $2) "
                "ON CONFLICT (chat_id, item_id) DO NOTHING",
                chat_id, item_id,
            )
            return True, used


async def release_free_usage(chat_id: int, item_id: int):
    """Return a reserved slot if processing fails."""
    if db_pool is not None:
        await db_pool.execute(
            "DELETE FROM usage_events WHERE chat_id = $1 AND item_id = $2",
            chat_id, item_id,
        )


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

    selected_key, (selected_label, selected_language) = await current_transcription_language(
        context,
        message.chat_id,
    )

    if selected_key is None:
        await message.reply_text(
            "🦝 Помоги Еноту услышать тебя точнее.\n\n"
            "🎙 Перед отправкой записи выбери язык аудио. "
            "От выбранного языка напрямую зависит качество распознавания.\n\n"
            "Если язык неизвестен, нажми «Определить автоматически».",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🎙 Выбрать язык аудио", callback_data="language_menu:main")]]
            ),
        )
        return

    duration = None
    if message.voice:
        duration = message.voice.duration
    elif message.audio:
        duration = message.audio.duration
    elif message.video:
        duration = message.video.duration

    if duration is not None and duration > 5 * 60:
        minutes = duration // 60
        seconds = duration % 60
        await message.reply_text(
            f"🦝 Эта запись длится {minutes}:{seconds:02d}. "
            "В бесплатном режиме можно наенотить файл продолжительностью не более 5 минут.",
            reply_markup=main_menu_keyboard(),
        )
        return

    # Reject declared oversize media before download.
    for media in (message.voice, message.audio, message.video, message.document):
        if media is not None and getattr(media, "file_size", None):
            if media.file_size > 25 * 1024 * 1024:
                await message.reply_text("🦝 Файл больше 25 МБ.")
                return

    if not await enforce_ai_rate_limit(update):
        return
    try:
        reserved, used_this_month = await reserve_free_usage(
            message.chat_id, message.message_id
        )
    except Exception:
        logger.exception("Quota database unavailable")
        await message.reply_text(
            "🦝 Учёт бесплатных запросов временно недоступен. "
            "Обработка приостановлена, чтобы не тратить запросы без контроля."
        )
        return
    if not reserved:
        await message.reply_text(
            "🦝 Бесплатный лимит на этот месяц уже использован: 4 из 4 файлов.\n\n"
            "Новый бесплатный лимит откроется в следующем календарном месяце.",
            reply_markup=main_menu_keyboard(),
        )
        return

    status = await message.reply_text(
        f"🦝 Енот уже разбирается… Бесплатно в этом месяце: {used_this_month + 1}/4"
    )

    try:
        telegram_file = await context.bot.get_file(file_id)

        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            suffix = ".ogg"

        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = Path(tmpdir) / f"racooon_upload{suffix}"
            await telegram_file.download_to_drive(custom_path=local_path)

            if local_path.stat().st_size > 25 * 1024 * 1024:
                await release_free_usage(message.chat_id, message.message_id)
                await status.edit_text(
                    "🦝 Файл больше 25 МБ. Пока отправь, пожалуйста, файл поменьше."
                )
                return

            with local_path.open("rb") as audio_file:
                language_prompts = {
                    "ru": "Русская речь. Транскрибируй дословно на русском языке. Не переводи и не меняй смысл.",
                    "en": "English speech. Transcribe verbatim in English. Do not translate or change the meaning.",
                    "he": "דיבור בעברית. תמלל במדויק בעברית. אל תתרגם ואל תשנה את המשמעות.",
                    "ar": "كلام باللغة العربية. انسخ الكلام حرفيًا بالعربية. لا تترجم ولا تغيّر المعنى.",
                    "fa": "گفتار فارسی. متن را دقیقاً به فارسی پیاده‌سازی کن. ترجمه نکن و معنی را تغییر نده.",
                }

                transcription_kwargs = {
                    "model": TRANSCRIPTION_MODEL,
                    "file": audio_file,
                    "prompt": language_prompts.get(
                        selected_language,
                        (
                            "Transcribe exactly in the language or languages spoken. "
                            "Do not translate. Preserve names, numbers, dates, times, and code-switching."
                        ),
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
            await release_free_usage(message.chat_id, message.message_id)
            await status.edit_text(
                "🦝 Я всё прослушал, но не смог уверенно распознать речь."
            )
            return

        try:
            structured = await structure_transcript(original, selected_language)
        except Exception:
            logger.exception("Structuring failed")
            structured = f"→ {original}"

        item_id = str(message.message_id)
        remember_transcript(context, item_id, original, selected_language)
        try:
            await save_transcript(
                message.chat_id,
                item_id,
                original,
                structured,
                selected_language,
            )
        except Exception:
            logger.exception("Persistent save failed")

        keyboard = result_keyboard(item_id)

        body = format_structured_text(structured)
        await status.edit_text(
            "🦝 <b>Енот всё разложил.</b>\n\n" + body,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception("Transcription failed")
        try:
            await release_free_usage(message.chat_id, message.message_id)
        except Exception:
            logger.exception("Quota release failed")
        await status.edit_text(
            "🦝 Что-то пошло не так при разборе файла. "
            "Попробуй ещё раз чуть позже."
        )


async def audio_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    if data.startswith("language_menu"):
        parts = data.split(":", 1)
        source = parts[1] if len(parts) == 2 and parts[1] else "main"

        _, (label, _) = await current_transcription_language(
            context,
            query.message.chat_id,
        )

        await query.message.reply_text(
            "🌐 <b>Выбор языка</b>\n\n"
            "<b>Ниже приведены языки, доступные для работы.\n"
            "Выбери один из них, чтобы Енот 🦝 как можно эффективнее и аккуратнее выполнил свою работу.</b>\n\n"
            "🔗 Затем добавь ссылку на <b>нужное видео / загрузи аудио- или видеофайл / запиши аудио 🎙</b>\n\n"
            "<b>Обрати, пожалуйста, внимание!</b>\n"
            "В рамках бесплатного пользования мы можем <b>наенотить не более 4 файлов в месяц продолжительностью не более 5 минут.</b>\n\n"
            "🦝 <b>Что там по монеткам?</b> — актуальные планы",
            parse_mode="HTML",
            reply_markup=language_keyboard(source),
        )
        return

    if data.startswith("lang:"):
        parts = data.split(":", 2)
        selected = parts[1] if len(parts) > 1 else ""
        source = parts[2] if len(parts) > 2 and parts[2] else "main"

        if selected not in TRANSCRIPTION_LANGUAGES:
            return

        await set_transcription_language(
            context,
            query.message.chat_id,
            selected,
        )
        label, _ = TRANSCRIPTION_LANGUAGES[selected]

        back_callback = (
            f"language_menu:{source}" if source != "main" else "language_menu:main"
        )
        nav = InlineKeyboardMarkup([navigation_row(back_callback)])

        if selected == "auto":
            await query.message.reply_text(
                "🦝 Язык аудио не выбран.\n"
                "Енот попробует определить его автоматически, но точность может быть ниже.\n"
                "Для лучшего результата выбери язык вручную перед отправкой записи.",
                reply_markup=nav,
            )
        else:
            await query.message.reply_text(
                f"🦝 Готово. Язык аудио: <b>{html.escape(label)}</b>.\n"
                "Этот выбор поможет Еноту точнее распознать речь. Теперь пришли запись.",
                parse_mode="HTML",
                reply_markup=nav,
            )
        return


async def library_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "library":
        rows = await load_recent_transcripts(query.message.chat_id)

        if not rows:
            await query.message.reply_text(
                "📚 <b>Мои записи</b>\n\n"
                "Здесь пока пусто. Пришли Еноту первую запись — и она появится здесь.",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            return

        buttons = [
            [InlineKeyboardButton(library_item_title(row), callback_data=f"library_item:{row['item_id']}")]
            for row in rows
        ]
        buttons.append([InlineKeyboardButton("🔎 Найти в записях", callback_data="library_search")])
        buttons.append(navigation_row("main_menu"))

        await query.message.reply_text(
            "📚 <b>Мои записи</b>\n\n"
            "Последние сохранённые записи:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data == "library_search":
        context.user_data["awaiting_library_search"] = True
        await query.message.reply_text(
            "🔎 <b>Что найти?</b>\n\n"
            "Напиши как помнишь — точные слова не обязательны. Например: «торт для Маши», «посылка» или «встреча в четверг».\n"
            "Енот поищет и по словам, и по смыслу. 🦝",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([navigation_row("library")]),
        )
        return

    if data.startswith("library_item:"):
        item_id = data.split(":", 1)[1]
        saved = await load_transcript(query.message.chat_id, item_id)

        if not saved:
            await query.message.reply_text(
                "🦝 Я не нашёл эту запись.",
                reply_markup=InlineKeyboardMarkup([navigation_row("library")]),
            )
            return

        original = saved["original"]
        structured = (saved["structured"] or "").strip()
        language_code = saved["language_code"]
        remember_transcript(context, item_id, original, language_code)

        body = format_structured_text(structured) if structured else html.escape(original)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Оригинал", callback_data=f"full:{item_id}"),
                    InlineKeyboardButton("Сделать заметки", callback_data=f"notes:{item_id}"),
                ],
                [
                    InlineKeyboardButton("Перевести", callback_data=f"translate:{item_id}"),
                ],
                [
                    InlineKeyboardButton("🎙 Выбрать язык аудио", callback_data=f"language_menu:{item_id}"),
                ],
                navigation_row("library"),
            ]
        )

        await query.message.reply_text(
            "📚 <b>Сохранённая запись</b>\n\n" + body,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return


async def source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data == "upload_media":
        await query.message.reply_text(
            "📎 <b>Загрузи файл прямо сюда.</b>\n\nМожно отправить голосовое, аудио или видео — Енот примет его в этом чате. 🦝",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([navigation_row("main_menu")]),
        )
        return
    if data == "submit_link":
        context.user_data["awaiting_media_link"] = True
        await query.message.reply_text(
            "🔗 <b>Пришли ссылку на видео.</b>\n\nПросто вставь её следующим сообщением. 🦝",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([navigation_row("main_menu")]),
        )
        return


async def navigation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    context.user_data.pop("awaiting_library_search", None)
    context.user_data.pop("awaiting_media_link", None)

    if data == "plans":
        await plans_callback(update, context)
        return

    if data == "main_menu":
        await query.message.reply_text(
            "🦝 <b>Главное меню</b>\n\n"
            "Пришли голосовое, аудио, видео или ссылку. "
            "Сохранённые материалы лежат в «Мои записи». "
            "Для более точного результата выбери язык аудио заранее.",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        return

    if data.startswith("result_menu:"):
        item_id = data.split(":", 1)[1]
        item = context.user_data.get("racooon_items", {}).get(item_id)

        if not item:
            try:
                saved = await load_transcript(query.message.chat_id, item_id)
            except Exception:
                logger.exception("Persistent load failed")
                saved = None

            if saved:
                original = saved["original"]
                language_code = saved["language_code"]
                remember_transcript(context, item_id, original, language_code)
            else:
                await query.message.reply_text(
                    "🦝 Я не нашёл эту запись.",
                    reply_markup=main_menu_keyboard(),
                )
                return

        await query.message.reply_text(
            "🦝 <b>Действия с записью</b>",
            parse_mode="HTML",
            reply_markup=result_keyboard(item_id),
        )
        return


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    action = parts[0]

    if action == "tr" and len(parts) == 3:
        _, target_language, item_id = parts
    elif len(parts) == 2:
        action, item_id = parts
        target_language = None
    else:
        return

    item = context.user_data.get("racooon_items", {}).get(item_id)

    if not item:
        try:
            saved = await load_transcript(query.message.chat_id, item_id)
        except Exception:
            logger.exception("Persistent load failed")
            saved = None

        if saved:
            original = saved["original"]
            language_code = saved["language_code"]
            remember_transcript(context, item_id, original, language_code)
            item = {
                "transcript": original,
                "language_code": language_code,
            }
        else:
            await query.message.reply_text(
                "🦝 Я не нашёл этот текст. Пришли файл ещё раз."
            )
            return

    if action in {"notes", "tr"} and not await enforce_ai_rate_limit(update):
        return

    original = item["transcript"]
    item_language_code = item.get("language_code")

    if action == "full":
        await send_long_text(
            query.message,
            "<b>Оригинал:</b>\n" + html.escape(original),
            parse_mode="HTML",
            reply_markup=output_navigation_keyboard(item_id),
        )
        return

    if action == "notes":
        status = await query.message.reply_text("🦝 Енот делает заметки…")
        try:
            notes = await make_notes(original, item_language_code)
            if not notes:
                notes = original
            await status.edit_text(
                "📝 <b>Заметки</b>\n\n" + html.escape(notes),
                parse_mode="HTML",
                reply_markup=output_navigation_keyboard(item_id),
            )
        except Exception:
            logger.exception("Notes failed")
            await status.edit_text(
                "🦝 Не получилось сделать заметки. Попробуй ещё раз чуть позже.",
                reply_markup=output_navigation_keyboard(item_id),
            )
        return

    if action == "translate":
        await query.message.reply_text(
            "🌍 Куда перевести?",
            reply_markup=translation_keyboard(item_id),
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
                reply_markup=output_navigation_keyboard(item_id),
            )
        except Exception:
            logger.exception("Translation failed")
            await status.edit_text(
                "🦝 Не получилось перевести. Попробуй ещё раз чуть позже.",
                reply_markup=output_navigation_keyboard(item_id),
            )


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.pop("awaiting_media_link", False):
        link = (update.message.text or "").strip()
        if not (link.startswith("http://") or link.startswith("https://")):
            await update.message.reply_text(
                "🦝 Это не похоже на ссылку. Нажми «Вставить ссылку» и пришли адрес, начинающийся с http:// или https://.",
                reply_markup=main_menu_keyboard(),
            )
            return
        await update.message.reply_text(
            "🔗 Ссылку поймал. 🦝\n\nЗагрузка видео по ссылке ещё не подключена, поэтому пока отправь само видео или аудиофайл в чат. Следующим шагом подключим разбор видео прямо по ссылке.",
            reply_markup=main_menu_keyboard(),
        )
        return

    if context.user_data.pop("awaiting_library_search", False):
        if not await enforce_ai_rate_limit(update):
            return
        search_text = (update.message.text or "").strip()
        rows = await search_transcripts(update.effective_chat.id, search_text)

        if not rows:
            await update.message.reply_text(
                "🦝 Ничего похожего не нашёл. Попробуй написать другими словами.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🔎 Искать ещё", callback_data="library_search")],
                        navigation_row("library"),
                    ]
                ),
            )
            return

        buttons = [
            [InlineKeyboardButton(library_item_title(row), callback_data=f"library_item:{row['item_id']}")]
            for row in rows
        ]
        buttons.append([InlineKeyboardButton("🔎 Искать ещё", callback_data="library_search")])
        buttons.append(navigation_row("library"))

        await update.message.reply_text(
            f"🦝 <b>Вот что я нашёл по запросу:</b> {html.escape(search_text)}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    await update.message.reply_text(
        "🦝 Я пока жду материал: голосовое, аудио, видео или ссылку.\n\n"
        "Если хочешь найти уже сохранённое — открой «Мои записи».",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(init_db)
        .post_shutdown(close_db)
        .build()
    )

    # Block group/channel processing before all normal handlers.
    app.add_handler(MessageHandler(~filters.ChatType.PRIVATE, block_non_private), group=-1)
    app.add_handler(CallbackQueryHandler(block_non_private), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("library", library_command))
    app.add_handler(
        CallbackQueryHandler(
            library_callback,
            pattern=r"^(library|library_search|library_item:.*)$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            source_callback,
            pattern=r"^(upload_media|submit_link)$",
        )
    )
    app.add_handler(CallbackQueryHandler(plans_callback, pattern=r"^plans$"))
    app.add_handler(CallbackQueryHandler(plan_detail_callback, pattern=r"^plan:(free|pocket|connected|work)$"))
    app.add_handler(
        CallbackQueryHandler(
            navigation_callback,
            pattern=r"^(main_menu|result_menu:)",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            audio_language_callback,
            pattern=r"^(language_menu(?::.*)?|lang:)",
        )
    )
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
