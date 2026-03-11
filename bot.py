"""
Telegram-бот «Дневник питания»
Стек: python-telegram-bot 20, Google Gemini Vision, SQLite
"""

import asyncio
import calendar
import json
import logging
from datetime import date, datetime

def _now() -> datetime:
    """Текущее время в локальной таймзоне (без tzinfo — для хранения в БД)."""
    return datetime.now(TIMEZONE).replace(tzinfo=None)


def _parse_dt(val) -> datetime:
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(val)

from telegram import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ai_analyzer import analyze_food_photo, analyze_food_text, get_daily_advice, get_photo_datetime, refine_analysis
from config import BOT_TOKEN, TIMEZONE
from database import (
    get_meal_by_id,
    get_meal_days_in_month,
    get_meals_for_date,
    get_or_create_user,
    init_db,
    save_meal,
    update_satiety,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.DEBUG,
)
# Снижаем шум от сторонних библиотек
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# ─── Шкалы голода и насыщения (10 баллов) ────────────────────────────────────
# Основа: методика осознанного питания, адаптирована к 10-балльной шкале.

HUNGER_GUIDE = (
    "*Шкала голода — как вы себя чувствуете прямо сейчас?*\n"
    "1 😌 Сыт, еда совсем не нужна\n"
    "2 🙂 Намёк на голод — тело начинает сигнализировать\n"
    "3 🙂 Лёгкий голод — замечаю, что скоро нужно поесть\n"
    "4 🍽 Голод нарастает — пора идти за едой ← *сигнал к действию*\n"
    "5 🍽 Готов сесть за стол и съесть тарелку целиком ← *идеально*\n"
    "6 😐 Ощущение пустоты, сосёт под ложечкой — нужна еда\n"
    "7 😤 Явный дискомфорт, трудно думать о другом\n"
    "8 ⚠️ Сахар падает: слабость, раздражительность — срочно перекусить!\n"
    "9 😨 Головокружение, сильная слабость — стресс для организма\n"
    "10 🚨 Тошнота, боль, темнеет в глазах — избегать любой ценой!"
)

SATIETY_GUIDE = (
    "*Шкала насыщения — что вы чувствуете прямо сейчас?*\n"
    "1 😋 Только начал есть, голод всё ещё сильный\n"
    "2 🙂 Голод немного ослаб — продолжаю есть\n"
    "3 😌 Голод отступает, насыщение начинается\n"
    "4 😌 Голода уже нет, но удовлетворения тоже нет — *замедлись!*\n"
    "5 🔔 Осознай: острой нужды нет, ты в безопасности — *лови этот момент*\n"
    "6 🌿 Чувствую приближение удовлетворения — ещё пару кусочков\n"
    "7 ✅ Удовлетворение: спокойно, хорошо, дышится легко ← *идеально, стоп!*\n"
    "8 😐 Немного лишнего — пояс начинает давить\n"
    "9 ⚠️ Переел: тяжесть, желудок переполнен, клонит в сон\n"
    "10 🚨 Живот болит, встать тяжело — так питаться нельзя!"
)

# Краткие метки для обратной связи после выбора
HUNGER_LABELS = {
    1:  "сыт, еда не нужна",
    2:  "намёк на голод",
    3:  "лёгкий голод — скоро нужно поесть",
    4:  "голод нарастает — идите готовить",
    5:  "готов к полноценному приёму пищи ✅",
    6:  "ощущение пустоты, сосёт под ложечкой",
    7:  "явный дискомфорт, трудно ждать",
    8:  "сахар падает, слабость ⚠️",
    9:  "головокружение, стресс для организма ⚠️",
    10: "кризис: тошнота, боль 🚨",
}

SATIETY_LABELS = {
    1:  "только начали есть, голод ещё сильный",
    2:  "голод немного ослаб",
    3:  "насыщение началось, голод отступает",
    4:  "голода нет, удовлетворения тоже — замедлитесь 🔔",
    5:  "осознайте: острой нужды нет, ловите этот момент 🔔",
    6:  "почти доволен, удовлетворение близко",
    7:  "удовлетворение достигнуто — стоп! ✅",
    8:  "слегка переели, пояс начинает давить",
    9:  "переели: тяжесть, клонит в сон ⚠️",
    10: "живот болит, встать тяжело 🚨",
}

# ─── Состояния диалога ────────────────────────────────────────────────────────

(
    MENU,
    AWAITING_HUNGER,
    AWAITING_PHOTO,
    AWAITING_CLARIFICATION,
    AWAITING_SATIETY,
    CALENDAR_VIEW,
    BULK_IMPORT,
) = range(7)


# ─── Клавиатуры ───────────────────────────────────────────────────────────────

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍽 Добавить приём пищи",  callback_data="meal:new")],
        [InlineKeyboardButton("📊 Анализ за сегодня",    callback_data="analysis:today")],
        [InlineKeyboardButton("📅 Календарь питания",    callback_data="calendar:open")],
        [InlineKeyboardButton("💡 Советы на сегодня",    callback_data="advice:today")],
        [InlineKeyboardButton("📥 Загрузить архив фото", callback_data="import:start")],
    ])


def kb_scale(prefix: str) -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(str(i), callback_data=f"{prefix}:{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=f"{prefix}:{i}") for i in range(6, 11)]
    return InlineKeyboardMarkup([row1, row2])


def kb_satiety_with_correct() -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(str(i), callback_data=f"satiety:{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=f"satiety:{i}") for i in range(6, 11)]
    return InlineKeyboardMarkup([
        row1, row2,
        [InlineKeyboardButton("✏️ Исправить анализ", callback_data="correct:analysis")],
    ])


def kb_back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")]])


def kb_calendar(user_id: int, year: int, month: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру-календарь, помечая дни с записями точками."""
    days_with_meals = get_meal_days_in_month(user_id, year, month)
    month_names = [
        "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
    ]
    rows: list[list[InlineKeyboardButton]] = []

    # Навигация по месяцам
    rows.append([
        InlineKeyboardButton("◀️", callback_data=f"cal:prev:{year}:{month}"),
        InlineKeyboardButton(f"{month_names[month]} {year}", callback_data="cal:noop"),
        InlineKeyboardButton("▶️", callback_data=f"cal:next:{year}:{month}"),
    ])

    # Заголовок дней недели
    rows.append([
        InlineKeyboardButton(d, callback_data="cal:noop")
        for d in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    ])

    # Дни месяца
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal:noop"))
            elif day in days_with_meals:
                row.append(InlineKeyboardButton(f"·{day}·", callback_data=f"cal:day:{year}:{month}:{day}"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"cal:day:{year}:{month}:{day}"))
        rows.append(row)

    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


# ─── Форматирование ───────────────────────────────────────────────────────────

def _score_emoji(score: int | None) -> str:
    if score is None:
        return "⚪"
    if score >= 8:
        return "🟢"
    if score >= 5:
        return "🟡"
    return "🔴"


def fmt_meal_card(analysis: dict) -> str:
    foods = ", ".join(analysis.get("food_items", [])) or "не определено"
    score = analysis.get("harvard_score")
    em = _score_emoji(score)

    lines = [
        f"🍴 *Блюда:* {foods}",
        f"🔥 *Калории:* ~{analysis.get('calories_estimate', '?')} ккал",
        (
            f"💪 Белки: {analysis.get('proteins_g', '?')} г  |  "
            f"🌾 Углеводы: {analysis.get('carbs_g', '?')} г  |  "
            f"🫒 Жиры: {analysis.get('fats_g', '?')} г"
        ),
        "",
        f"{em} *Гарвардская тарелка: {score}/10*",
        f"🥗 Овощи/фрукты: {analysis.get('vegetables_percent', '?')}%",
        f"🌾 Злаки: {analysis.get('grains_percent', '?')}%",
        f"🍗 Белок: {analysis.get('protein_percent', '?')}%",
    ]

    missing = analysis.get("what_missing", [])
    if missing:
        lines += ["", f"💡 *Чего не хватает:* {', '.join(missing)}"]

    ha = analysis.get("harvard_analysis", "")
    if ha:
        lines += ["", f"_{ha}_"]

    recs = analysis.get("recommendations", [])
    if recs:
        lines += ["", "📝 *Рекомендации:*"]
        lines += [f"• {r}" for r in recs]

    return "\n".join(lines)


def fmt_day_summary(meals: list[dict]) -> str:
    total_cal = sum(m.get("calories_estimate") or 0 for m in meals)
    total_p   = sum(m.get("proteins_g") or 0 for m in meals)
    total_f   = sum(m.get("fats_g") or 0 for m in meals)
    total_c   = sum(m.get("carbs_g") or 0 for m in meals)
    scores    = [m["harvard_score"] for m in meals if m.get("harvard_score")]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    lines = [
        f"🔥 Калорий за день: *~{total_cal} ккал*",
        f"💪 Белки: {total_p:.0f} г  |  🌾 Углеводы: {total_c:.0f} г  |  🫒 Жиры: {total_f:.0f} г",
        "",
        f"{_score_emoji(int(avg_score) if avg_score else None)} *Средняя оценка тарелки: {avg_score or '?'}/10*",
        "",
        "*Приёмы пищи:*",
    ]
    for i, m in enumerate(meals, 1):
        t = _parse_dt(m["meal_time"]).strftime("%H:%M")
        foods = json.loads(m.get("food_items") or "[]")
        food_str = ", ".join(foods[:2]) if foods else "не определено"
        lines.append(f"{i}. {t} — {food_str} (~{m.get('calories_estimate') or '?'} ккал)")
    return "\n".join(lines)


def fmt_meal_detail(m: dict) -> str:
    foods = json.loads(m.get("food_items") or "[]")
    t = _parse_dt(m["meal_time"]).strftime("%d.%m.%Y %H:%M")
    score = m.get("harvard_score")

    lines = [
        f"🍽 *Детали приёма пищи*",
        f"_{t}_",
        "",
        f"*Блюда:* {', '.join(foods) or 'не определено'}",
        f"📝 {m.get('description') or ''}",
        "",
        f"🔥 *Калории:* ~{m.get('calories_estimate') or '?'} ккал",
        f"💪 Белки: {m.get('proteins_g') or '?'} г",
        f"🌾 Углеводы: {m.get('carbs_g') or '?'} г",
        f"🫒 Жиры: {m.get('fats_g') or '?'} г",
        "",
        f"{_score_emoji(score)} *Гарвардская тарелка: {score or '?'}/10*",
        f"🥗 Овощи/фрукты: {m.get('vegetables_percent') or '?'}%",
        f"🌾 Злаки: {m.get('grains_percent') or '?'}%",
        f"🍗 Белок: {m.get('protein_percent') or '?'}%",
    ]

    if m.get("harvard_analysis"):
        lines += ["", f"📋 {m['harvard_analysis']}"]

    if m.get("hunger_before") or m.get("satiety_after"):
        lines += [
            "",
            f"😋 Голод до: {m.get('hunger_before') or '?'}/10  →  "
            f"Насыщение после: {m.get('satiety_after') or '?'}/10",
        ]

    missing = json.loads(m.get("what_missing") or "[]")
    if missing:
        lines += ["", f"💡 *Чего не хватало:* {', '.join(missing)}"]

    return "\n".join(lines)


# ─── Хэндлеры ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    context.user_data.clear()

    text = (
        f"Привет, *{user.first_name}*! 👋\n\n"
        "Я — ваш персональный *Дневник питания*.\n\n"
        "Что умею:\n"
        "• 📸 Распознаю еду по фото (Google Gemini AI)\n"
        "• 📊 Оцениваю рацион по *Гарвардской тарелке*\n"
        "• 😋 Отслеживаю голод и насыщение\n"
        "• 💡 Даю персональные советы по питанию\n"
        "• 📅 Веду историю приёмов пищи с календарём\n\n"
        "Выберите действие:"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_main_menu())
    return MENU


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /add — сразу начать добавление приёма пищи."""
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    context.user_data.clear()
    context.user_data["meal_start"] = _now().isoformat()
    await update.message.reply_text(
        f"🍽 *Новый приём пищи*\n\n{HUNGER_GUIDE}\n\nВыберите цифру:",
        parse_mode="Markdown",
        reply_markup=kb_scale("hunger"),
    )
    return AWAITING_HUNGER


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /today — анализ за сегодня."""
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    meals = get_meals_for_date(user.id, date.today())
    if not meals:
        await update.message.reply_text(
            "📊 *Анализ за сегодня*\n\nПока нет записей. Добавьте первый приём пищи! 🍽",
            parse_mode="Markdown",
            reply_markup=kb_main_menu(),
        )
        return MENU
    today_str = date.today().strftime("%d.%m.%Y")
    text = f"📊 *Сводка за {today_str}*\n🍽 Приёмов пищи: *{len(meals)}*\n\n{fmt_day_summary(meals)}"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Получить советы", callback_data="advice:today")],
        [InlineKeyboardButton("🏠 Главное меню",    callback_data="menu:main")],
    ]))
    return MENU


async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /calendar — открыть календарь."""
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    now = _now()
    await update.message.reply_text(
        "📅 *Календарь питания*\n\nДни с записями отмечены точками ·\nНажмите на день, чтобы посмотреть записи:",
        parse_mode="Markdown",
        reply_markup=kb_calendar(user.id, now.year, now.month),
    )
    return CALENDAR_VIEW


async def cmd_advice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /advice — советы на сегодня."""
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    meals = get_meals_for_date(user.id, date.today())
    if not meals:
        await update.message.reply_text(
            "💡 Ещё нет записей за сегодня. Добавьте хотя бы один приём пищи!",
            reply_markup=kb_main_menu(),
        )
        return MENU
    wait_msg = await update.message.reply_text("💭 Формирую персональные советы…")
    meals_data = [
        {
            "meal_time":          _parse_dt(m["meal_time"]).strftime("%H:%M"),
            "food_items":         json.loads(m.get("food_items") or "[]"),
            "hunger_before":      m.get("hunger_before"),
            "satiety_after":      m.get("satiety_after"),
            "calories_estimate":  m.get("calories_estimate"),
            "harvard_score":      m.get("harvard_score"),
            "vegetables_percent": m.get("vegetables_percent"),
            "grains_percent":     m.get("grains_percent"),
            "protein_percent":    m.get("protein_percent"),
        }
        for m in meals
    ]
    try:
        advice = await get_daily_advice(meals_data)
    except Exception as exc:
        logger.error("Ошибка советов: %s", exc)
        advice = "Не удалось получить советы. Попробуйте позже."
    await wait_msg.delete()
    try:
        await update.message.reply_text(
            f"💡 *Советы по питанию за сегодня*\n\n{advice}",
            parse_mode="Markdown",
            reply_markup=kb_back_to_menu(),
        )
    except Exception:
        await update.message.reply_text(
            f"💡 Советы по питанию за сегодня\n\n{advice}",
            reply_markup=kb_back_to_menu(),
        )
    return MENU


async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Команда /import — загрузить архив фото."""
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    context.user_data.clear()
    context.user_data["import_count"] = 0
    context.user_data["import_days"] = set()
    await update.message.reply_text(
        "📥 *Загрузка архива*\n\n"
        "Три способа добавить запись:\n\n"
        "📎 *Только фото-файл* — отправьте фото как файл\n"
        "_Дата съёмки считается из метаданных автоматически_\n\n"
        "📎✍️ *Фото-файл + описание* — при отправке файла добавьте подпись\n\n"
        "✍️ *Только текст* — напишите что ели, без фото\n\n"
        "Когда загрузите всё — нажмите «Готово».",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Готово", callback_data="import:done")],
            [InlineKeyboardButton("🏠 Отмена",  callback_data="menu:main")],
        ]),
    )
    return BULK_IMPORT


async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("Главное меню:", reply_markup=kb_main_menu())
    return MENU


# ── Добавление приёма пищи ────────────────────────────────────────────────────

async def cb_meal_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["meal_start"] = _now().isoformat()

    await query.edit_message_text(
        f"🍽 *Новый приём пищи*\n\n"
        f"{HUNGER_GUIDE}\n\n"
        "Выберите цифру:",
        parse_mode="Markdown",
        reply_markup=kb_scale("hunger"),
    )
    return AWAITING_HUNGER


async def cb_hunger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    hunger = int(query.data.split(":")[1])
    context.user_data["hunger"] = hunger

    label = HUNGER_LABELS.get(hunger, "")

    await query.edit_message_text(
        f"Голод: *{hunger}/10* — {label}\n\n"
        "Как записать приём пищи:\n\n"
        "📸 *Только фото* — просто отправьте фото\n\n"
        "✍️ *Только текст* — напишите что ели\n"
        "_Пример: «гречка с курицей и салатом»_\n\n"
        "📸✍️ *Фото + описание* — сначала напишите текст, потом отправьте фото\n"
        "_Описание помогает ИИ точнее распознать блюда_",
        parse_mode="Markdown",
    )
    return AWAITING_PHOTO


async def msg_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo[-1]
    file_id = photo.file_id
    # Описание: из подписи к фото или из ранее сохранённого текста
    caption = update.message.caption or context.user_data.pop("photo_description", None)

    wait_msg = await update.message.reply_text(
        "🔍 *Анализирую вашу тарелку…*\n_Обычно несколько секунд, максимум ~1 минута_",
        parse_mode="Markdown",
    )

    try:
        file = await context.bot.get_file(file_id)
        raw = bytes(await file.download_as_bytearray())
        analysis = await analyze_food_photo(raw, description=caption)
    except Exception as exc:
        logger.error("Ошибка анализа фото: %s", exc)
        await wait_msg.edit_text(
            f"😔 Не удалось проанализировать фото.\n\n"
            f"Причина: `{type(exc).__name__}: {exc}`\n\n"
            "Попробуйте отправить другое фото или повторите через минуту.",
            parse_mode="Markdown",
        )
        return AWAITING_PHOTO

    context.user_data["photo_file_id"] = file_id
    context.user_data["analysis"] = analysis
    await wait_msg.delete()

    # Всегда показываем карточку и рекомендации
    await update.message.reply_photo(photo=file_id)
    await update.message.reply_text(fmt_meal_card(analysis), parse_mode="Markdown")

    questions = analysis.get("questions", [])
    if questions:
        q_text = "\n".join(f"• {q}" for q in questions)
        await update.message.reply_text(
            f"🤔 Хочу уточнить — это сделает анализ точнее:\n{q_text}\n\n"
            "Ответьте одним сообщением или пропустите:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ Пропустить уточнение", callback_data="skip:clarification")],
            ]),
        )
        return AWAITING_CLARIFICATION

    await update.message.reply_text(
        f"Приятного аппетита! 🍽\n\n"
        f"{SATIETY_GUIDE}\n\n"
        "Выберите цифру после еды:",
        parse_mode="Markdown",
        reply_markup=kb_satiety_with_correct(),
    )
    return AWAITING_SATIETY


async def msg_text_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь написал описание еды — сохраняем и предлагаем отправить фото."""
    description = update.message.text
    context.user_data["photo_description"] = description

    await update.message.reply_text(
        f"✍️ Описание сохранено: _{description}_\n\n"
        "📸 Теперь отправьте фото для совместного анализа,\n"
        "или нажмите кнопку ниже чтобы проанализировать только по тексту.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Анализировать без фото", callback_data="analyze:text_only")],
        ]),
    )
    return AWAITING_PHOTO


async def cb_analyze_text_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Анализ по сохранённому текстовому описанию без фото."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    description = context.user_data.get("photo_description", "")
    if not description:
        await query.edit_message_text("❌ Описание не найдено. Напишите что ели.")
        return AWAITING_PHOTO

    wait_msg = await query.edit_message_text(
        "🔍 *Анализирую по описанию…*\n_Обычно несколько секунд, максимум ~1 минута_",
        parse_mode="Markdown",
    )

    try:
        analysis = await analyze_food_text(description)
    except Exception as exc:
        logger.error("Ошибка анализа текста: %s", exc)
        await wait_msg.edit_text(
            f"😔 Не удалось проанализировать.\n\nПричина: `{type(exc).__name__}: {exc}`",
            parse_mode="Markdown",
        )
        return AWAITING_PHOTO

    context.user_data["photo_file_id"] = None
    context.user_data["analysis"] = analysis
    context.user_data.pop("photo_description", None)

    # Всегда показываем карточку и рекомендации
    await query.message.reply_text(fmt_meal_card(analysis), parse_mode="Markdown")

    questions = analysis.get("questions", [])
    if questions:
        q_text = "\n".join(f"• {q}" for q in questions)
        await query.message.reply_text(
            f"🤔 Хочу уточнить — это сделает анализ точнее:\n{q_text}\n\n"
            "Ответьте одним сообщением или пропустите:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭ Пропустить уточнение", callback_data="skip:clarification")],
            ]),
        )
        return AWAITING_CLARIFICATION

    await query.message.reply_text(
        f"Приятного аппетита! 🍽\n\n{SATIETY_GUIDE}\n\nВыберите цифру после еды:",
        parse_mode="Markdown",
        reply_markup=kb_satiety_with_correct(),
    )
    return AWAITING_SATIETY


async def msg_clarification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answers = update.message.text
    analysis = context.user_data.get("analysis", {})
    questions = analysis.get("questions", [])
    correcting = context.user_data.pop("correcting", False)

    wait_msg = await update.message.reply_text("🔄 Уточняю анализ…")
    try:
        refined = await refine_analysis(analysis, questions, answers)
        context.user_data["analysis"] = refined
    except Exception as exc:
        logger.error("Ошибка уточнения: %s", exc)
        refined = analysis

    await wait_msg.delete()

    file_id = context.user_data.get("photo_file_id")
    if file_id:
        await update.message.reply_photo(photo=file_id)
    await update.message.reply_text(fmt_meal_card(refined), parse_mode="Markdown")

    header = "✅ Анализ обновлён!" if correcting else "Приятного аппетита! 😊"
    await update.message.reply_text(
        f"{header}\n\n{SATIETY_GUIDE}\n\nВыберите цифру после еды:",
        parse_mode="Markdown",
        reply_markup=kb_satiety_with_correct(),
    )
    return AWAITING_SATIETY


async def cb_skip_clarification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь пропустил уточняющие вопросы."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        f"Приятного аппетита! 🍽\n\n{SATIETY_GUIDE}\n\nВыберите цифру после еды:",
        parse_mode="Markdown",
        reply_markup=kb_satiety_with_correct(),
    )
    return AWAITING_SATIETY


async def cb_correct_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь хочет исправить анализ — просим написать что не так."""
    query = update.callback_query
    await query.answer()
    context.user_data["correcting"] = True

    await query.message.reply_text(
        "✏️ *Что нужно исправить?*\n\n"
        "Напишите что именно ИИ определил неправильно.\n"
        "_Например: «это не тост, а омлет» или «добавь 2 варёных яйца»_",
        parse_mode="Markdown",
    )
    return AWAITING_CLARIFICATION


async def cb_satiety(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    satiety = int(query.data.split(":")[1])
    user_id = update.effective_user.id
    analysis = context.user_data.get("analysis", {})
    hunger = context.user_data.get("hunger", 5)

    meal_data = {
        "photo_file_id":      context.user_data.get("photo_file_id"),
        "meal_time":          context.user_data.get("meal_start", _now().isoformat()),
        "hunger_before":      hunger,
        "food_items":         analysis.get("food_items", []),
        "description":        analysis.get("description"),
        "calories_estimate":  analysis.get("calories_estimate"),
        "proteins_g":         analysis.get("proteins_g"),
        "fats_g":             analysis.get("fats_g"),
        "carbs_g":            analysis.get("carbs_g"),
        "harvard_score":      analysis.get("harvard_score"),
        "harvard_analysis":   analysis.get("harvard_analysis"),
        "vegetables_percent": analysis.get("vegetables_percent"),
        "grains_percent":     analysis.get("grains_percent"),
        "protein_percent":    analysis.get("protein_percent"),
        "what_missing":       analysis.get("what_missing", []),
        "ai_questions":       analysis.get("questions", []),
    }
    meal_id = save_meal(user_id, meal_data)
    update_satiety(meal_id, satiety)
    context.user_data.clear()

    satiety_label = SATIETY_LABELS.get(satiety, "")

    # Подсказка по паттерну голод/насыщение
    tip = ""
    if hunger <= 3 and satiety >= 8:
        tip = (
            "\n\n💡 _Вы поели почти без голода и наелись до отвала. "
            "Попробуйте начинать есть при голоде 5–6, а заканчивать на 7._"
        )
    elif hunger >= 8:
        tip = (
            "\n\n💡 _Сильный голод (8+) часто приводит к перееданию. "
            "Старайтесь не доводить себя до этого состояния._"
        )
    elif satiety >= 9:
        tip = (
            "\n\n💡 _Вы переели. В следующий раз замедлитесь на 5–6 баллах — "
            "там легко поймать момент удовлетворения._"
        )
    elif satiety <= 4:
        tip = (
            "\n\n💡 _Похоже, вы не наелись. Проверьте: достаточно ли белка и "
            "овощей в тарелке? Они дают длительное насыщение._"
        )

    await query.edit_message_text(
        f"Насыщение: *{satiety}/10* — {satiety_label}\n\n"
        f"✅ Приём пищи сохранён в дневник!{tip}",
        parse_mode="Markdown",
        reply_markup=kb_main_menu(),
    )
    return MENU


# ── Анализ за сегодня ─────────────────────────────────────────────────────────

async def cb_analysis_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    meals = get_meals_for_date(user_id, date.today())

    if not meals:
        await query.edit_message_text(
            "📊 *Анализ за сегодня*\n\nПока нет записей. Добавьте первый приём пищи! 🍽",
            parse_mode="Markdown",
            reply_markup=kb_main_menu(),
        )
        return MENU

    today_str = date.today().strftime("%d.%m.%Y")
    text = f"📊 *Сводка за {today_str}*\n🍽 Приёмов пищи: *{len(meals)}*\n\n"
    text += fmt_day_summary(meals)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Получить советы", callback_data="advice:today")],
        [InlineKeyboardButton("🏠 Главное меню",    callback_data="menu:main")],
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    return MENU


# ── Советы за сегодня ─────────────────────────────────────────────────────────

async def cb_advice_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    meals = get_meals_for_date(user_id, date.today())

    if not meals:
        await query.edit_message_text(
            "💡 Ещё нет записей за сегодня. Добавьте хотя бы один приём пищи!",
            reply_markup=kb_main_menu(),
        )
        return MENU

    await query.edit_message_text("💭 Формирую персональные советы через Gemini AI…")

    meals_data = [
        {
            "meal_time":          _parse_dt(m["meal_time"]).strftime("%H:%M"),
            "food_items":         json.loads(m.get("food_items") or "[]"),
            "hunger_before":      m.get("hunger_before"),
            "satiety_after":      m.get("satiety_after"),
            "calories_estimate":  m.get("calories_estimate"),
            "harvard_score":      m.get("harvard_score"),
            "vegetables_percent": m.get("vegetables_percent"),
            "grains_percent":     m.get("grains_percent"),
            "protein_percent":    m.get("protein_percent"),
        }
        for m in meals
    ]

    try:
        advice = await get_daily_advice(meals_data)
    except Exception as exc:
        logger.error("Ошибка советов: %s", exc)
        advice = "Не удалось получить советы. Попробуйте позже."

    try:
        await query.edit_message_text(
            f"💡 *Советы по питанию за сегодня*\n\n{advice}",
            parse_mode="Markdown",
            reply_markup=kb_back_to_menu(),
        )
    except Exception:
        await query.edit_message_text(
            f"💡 Советы по питанию за сегодня\n\n{advice}",
            reply_markup=kb_back_to_menu(),
        )
    return MENU


# ── Календарь ─────────────────────────────────────────────────────────────────

async def cb_calendar_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    now = _now()

    await query.edit_message_text(
        "📅 *Календарь питания*\n\n"
        "Дни с записями отмечены точками ·\n"
        "Нажмите на день, чтобы посмотреть записи:",
        parse_mode="Markdown",
        reply_markup=kb_calendar(user_id, now.year, now.month),
    )
    return CALENDAR_VIEW


async def cb_calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    parts = query.data.split(":")  # cal:prev/next/day/noop : year : month [: day]
    action = parts[1]

    if action == "noop":
        return CALENDAR_VIEW

    year, month = int(parts[2]), int(parts[3])

    if action == "prev":
        month -= 1
        if month < 1:
            month, year = 12, year - 1
    elif action == "next":
        month += 1
        if month > 12:
            month, year = 1, year + 1
    elif action == "day":
        day = int(parts[4])
        selected = date(year, month, day)
        context.user_data["cal_date"] = selected.isoformat()
        return await _show_day(query, user_id, selected, context)

    await query.edit_message_reply_markup(reply_markup=kb_calendar(user_id, year, month))
    return CALENDAR_VIEW


async def _show_day(
    query: CallbackQuery,
    user_id: int,
    selected: date,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    meals = get_meals_for_date(user_id, selected)
    date_str = selected.strftime("%d.%m.%Y")

    if not meals:
        await query.edit_message_text(
            f"📅 *{date_str}*\n\nВ этот день записей нет.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ К календарю", callback_data="calendar:open")],
            ]),
        )
        return CALENDAR_VIEW

    text = f"📅 *{date_str}*  •  приёмов пищи: {len(meals)}\n\n"
    text += fmt_day_summary(meals)

    buttons = []
    for i, m in enumerate(meals, 1):
        t = _parse_dt(m["meal_time"]).strftime("%H:%M")
        buttons.append([
            InlineKeyboardButton(
                f"👁 Приём {i} ({t})",
                callback_data=f"meal:detail:{m['id']}",
            )
        ])
    buttons.append([InlineKeyboardButton("◀️ К календарю", callback_data="calendar:open")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
    return CALENDAR_VIEW


async def cb_meal_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    meal_id = int(query.data.split(":")[2])
    meal = get_meal_by_id(meal_id)

    if not meal:
        await query.answer("Запись не найдена", show_alert=True)
        return CALENDAR_VIEW

    text = fmt_meal_detail(meal)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="cal:back")]])

    # Пробуем показать фото с подписью
    if meal.get("photo_file_id"):
        try:
            await query.message.reply_photo(
                photo=meal["photo_file_id"],
                caption=text,
                parse_mode="Markdown",
                reply_markup=kb,
            )
            await query.message.delete()
            return CALENDAR_VIEW
        except Exception as e:
            logger.warning("Не удалось показать фото: %s", e)
            try:
                await query.message.reply_photo(
                    photo=meal["photo_file_id"],
                    caption=text,
                    reply_markup=kb,
                )
                await query.message.delete()
                return CALENDAR_VIEW
            except Exception as e2:
                logger.warning("Фото недоступно: %s", e2)

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    return CALENDAR_VIEW


async def cb_cal_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Возврат из деталей приёма пищи к выбранному дню."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    stored = context.user_data.get("cal_date")
    if stored:
        selected = date.fromisoformat(stored)
        return await _show_day(query, user_id, selected, context)

    # Если день не сохранён — возвращаемся в текущий месяц
    now = _now()
    await query.edit_message_text(
        "📅 *Календарь питания*",
        parse_mode="Markdown",
        reply_markup=kb_calendar(user_id, now.year, now.month),
    )
    return CALENDAR_VIEW


# ── Загрузка архива фото ──────────────────────────────────────────────────────

async def cb_import_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["import_count"] = 0
    context.user_data["import_days"] = set()

    await query.edit_message_text(
        "📥 *Загрузка архива*\n\n"
        "Три способа добавить запись:\n\n"
        "📎 *Только фото-файл* — отправьте фото как файл\n"
        "_Дата съёмки считается из метаданных автоматически_\n\n"
        "📎✍️ *Фото-файл + описание* — при отправке файла добавьте подпись\n"
        "_В Telegram: выберите файл → поле «Подпись» внизу → напишите что ели_\n\n"
        "✍️ *Только текст* — напишите что ели, без фото\n\n"
        "Как отправить фото как файл:\n"
        "• Нажмите 📎 → *Файл* (не «Фото»!) → выберите снимок\n"
        "• На телефоне: скрепка → Документ\n\n"
        "_Голод и насыщение для архивных записей не фиксируются._\n\n"
        "Когда загрузите всё — нажмите «Готово».",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Готово", callback_data="import:done")],
            [InlineKeyboardButton("🏠 Отмена",  callback_data="menu:main")],
        ]),
    )
    return BULK_IMPORT


def _make_import_meal_data(analysis: dict, file_id: str | None, meal_dt: datetime) -> dict:
    return {
        "photo_file_id":      file_id,
        "meal_time":          meal_dt.isoformat(),
        "hunger_before":      None,
        "food_items":         analysis.get("food_items", []),
        "description":        analysis.get("description"),
        "calories_estimate":  analysis.get("calories_estimate"),
        "proteins_g":         analysis.get("proteins_g"),
        "fats_g":             analysis.get("fats_g"),
        "carbs_g":            analysis.get("carbs_g"),
        "harvard_score":      analysis.get("harvard_score"),
        "harvard_analysis":   analysis.get("harvard_analysis"),
        "vegetables_percent": analysis.get("vegetables_percent"),
        "grains_percent":     analysis.get("grains_percent"),
        "protein_percent":    analysis.get("protein_percent"),
        "what_missing":       analysis.get("what_missing", []),
        "ai_questions":       [],
    }


_IMPORT_CONFIRM_KB = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("✅ Сохранить", callback_data="import:save"),
        InlineKeyboardButton("✏️ Исправить", callback_data="import:correct"),
    ],
    [InlineKeyboardButton("❌ Пропустить", callback_data="import:discard")],
])


async def msg_import_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    caption = update.message.caption or None

    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text(
            "⚠️ Это не изображение. Отправляйте фото в формате JPG, PNG и т.п."
        )
        return BULK_IMPORT

    wait_msg = await update.message.reply_text("🔍 Читаю фото и анализирую…\n_Максимум ~1 минута_", parse_mode="Markdown")

    try:
        file = await context.bot.get_file(doc.file_id)
        raw = bytes(await file.download_as_bytearray())
    except Exception as exc:
        logger.error("Ошибка скачивания файла: %s", exc)
        await wait_msg.edit_text("❌ Не удалось скачать файл. Попробуйте ещё раз.")
        return BULK_IMPORT

    meal_dt = get_photo_datetime(raw)
    if meal_dt:
        date_note = f"📅 {meal_dt.strftime('%d.%m.%Y %H:%M')} _(из метаданных фото)_"
    else:
        meal_dt = _now()
        date_note = f"📅 {meal_dt.strftime('%d.%m.%Y %H:%M')} _(EXIF не найден, использую текущее время)_"

    try:
        analysis = await analyze_food_photo(raw, description=caption)
    except Exception as exc:
        logger.error("Ошибка анализа фото: %s", exc)
        await wait_msg.edit_text(
            f"❌ Не удалось проанализировать фото.\n\n"
            f"Причина: `{type(exc).__name__}: {exc}`\n\n"
            "Попробуйте отправить другое фото или повторите позже.",
            parse_mode="Markdown",
        )
        return BULK_IMPORT

    context.user_data["pending_import"] = {
        "meal_data": _make_import_meal_data(analysis, doc.file_id, meal_dt),
        "analysis":  analysis,
        "date_note": date_note,
    }

    await wait_msg.delete()
    await update.message.reply_document(document=doc.file_id)
    confirm_text = f"{date_note}\n\n{fmt_meal_card(analysis)}\n\nСохранить эту запись?"
    try:
        await update.message.reply_text(confirm_text, parse_mode="Markdown", reply_markup=_IMPORT_CONFIRM_KB)
    except Exception:
        await update.message.reply_text(confirm_text, reply_markup=_IMPORT_CONFIRM_KB)
    return BULK_IMPORT


async def msg_import_photo_warning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь прислал сжатое фото вместо файла — объясняем."""
    await update.message.reply_text(
        "⚠️ Вы отправили фото как изображение — Telegram сжимает его и стирает дату съёмки.\n\n"
        "Чтобы я прочитал дату из метаданных, отправьте то же фото *как файл*:\n"
        "📎 → *Файл* (или *Документ*) → выберите фото",
        parse_mode="Markdown",
    )
    return BULK_IMPORT


async def msg_import_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Текст в режиме импорта: либо исправление, либо новое описание."""
    text_input = update.message.text

    # Режим исправления: пользователь уточняет уже проанализированную запись
    if context.user_data.pop("import_correcting", False):
        pending = context.user_data.get("pending_import", {})
        prev_analysis = pending.get("analysis", {})
        wait_msg = await update.message.reply_text("🔄 Уточняю анализ…")
        try:
            refined = await refine_analysis(prev_analysis, prev_analysis.get("questions", []), text_input)
        except Exception as exc:
            logger.error("Ошибка уточнения: %s", exc)
            refined = prev_analysis
        await wait_msg.delete()

        meal_dt = datetime.fromisoformat(pending["meal_data"]["meal_time"])
        pending["analysis"] = refined
        pending["meal_data"] = _make_import_meal_data(refined, pending["meal_data"].get("photo_file_id"), meal_dt)
        context.user_data["pending_import"] = pending

        confirm_text = f"{pending['date_note']}\n\n{fmt_meal_card(refined)}\n\n✅ Анализ обновлён. Сохранить?"
        try:
            await update.message.reply_text(confirm_text, parse_mode="Markdown", reply_markup=_IMPORT_CONFIRM_KB)
        except Exception:
            await update.message.reply_text(confirm_text, reply_markup=_IMPORT_CONFIRM_KB)
        return BULK_IMPORT

    # Обычный режим: новое текстовое описание
    wait_msg = await update.message.reply_text(
        "🔍 Анализирую по описанию…\n_Максимум ~1 минута_",
        parse_mode="Markdown",
    )

    try:
        analysis = await analyze_food_text(text_input)
    except Exception as exc:
        logger.error("Ошибка анализа текста: %s", exc)
        await wait_msg.edit_text(
            f"❌ Не удалось проанализировать.\n\nПричина: `{type(exc).__name__}: {exc}`",
            parse_mode="Markdown",
        )
        return BULK_IMPORT

    meal_dt = _now()
    date_note = f"📅 {meal_dt.strftime('%d.%m.%Y %H:%M')}"

    context.user_data["pending_import"] = {
        "meal_data": _make_import_meal_data(analysis, None, meal_dt),
        "analysis":  analysis,
        "date_note": date_note,
    }

    await wait_msg.delete()
    confirm_text = f"{date_note}\n\n{fmt_meal_card(analysis)}\n\nСохранить эту запись?"
    try:
        await update.message.reply_text(confirm_text, parse_mode="Markdown", reply_markup=_IMPORT_CONFIRM_KB)
    except Exception:
        await update.message.reply_text(confirm_text, reply_markup=_IMPORT_CONFIRM_KB)
    return BULK_IMPORT


async def cb_import_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохранить ожидающую запись из архива."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    pending = context.user_data.pop("pending_import", None)
    if not pending:
        await query.message.reply_text("⚠️ Нет записи для сохранения.")
        return BULK_IMPORT

    user_id = update.effective_user.id
    try:
        save_meal(user_id, pending["meal_data"])
    except Exception as exc:
        logger.error("Ошибка сохранения записи импорта: %s", exc)
        await query.message.reply_text(f"❌ Не удалось сохранить запись.\n\nПричина: {exc}")
        return BULK_IMPORT

    count = context.user_data.get("import_count", 0) + 1
    days: set = context.user_data.get("import_days", set())
    meal_dt = datetime.fromisoformat(pending["meal_data"]["meal_time"])
    days.add(meal_dt.date().isoformat())
    context.user_data["import_count"] = count
    context.user_data["import_days"] = days

    foods = ", ".join(pending["analysis"].get("food_items", [])) or "не определено"
    score = pending["analysis"].get("harvard_score")
    success_text = (
        f"✅ Запись #{count} сохранена\n"
        f"{pending['date_note']}\n"
        f"🍴 {foods}\n"
        f"🔥 ~{pending['analysis'].get('calories_estimate', '?')} ккал  {_score_emoji(score)} {score}/10\n\n"
        "Отправьте следующее фото/описание или нажмите Готово."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="import:done")]])

    try:
        await query.edit_message_text(success_text, reply_markup=kb)
    except Exception as exc:
        logger.warning("edit_message_text failed in cb_import_save: %s", exc)
        await query.message.reply_text(success_text, reply_markup=kb)

    return BULK_IMPORT


async def cb_import_correct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пользователь хочет исправить анализ перед сохранением."""
    query = update.callback_query
    await query.answer()
    context.user_data["import_correcting"] = True

    await query.message.reply_text(
        "✏️ *Что нужно исправить?*\n\n"
        "Напишите что ИИ определил неправильно.\n"
        "_Например: «это не тост, а омлет» или «добавь 100г риса»_",
        parse_mode="Markdown",
    )
    return BULK_IMPORT


async def cb_import_discard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пропустить текущую запись."""
    query = update.callback_query
    await query.answer("Запись пропущена")
    context.user_data.pop("pending_import", None)
    context.user_data.pop("import_correcting", None)

    await query.edit_message_text(
        "❌ Запись пропущена.\n\n_Отправьте следующее фото/описание или нажмите «Готово»._",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Готово", callback_data="import:done")],
        ]),
    )
    return BULK_IMPORT


async def cb_import_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    count = context.user_data.get("import_count", 0)
    days = context.user_data.get("import_days", set())

    if count == 0:
        text = "Вы не загрузили ни одного фото."
    else:
        text = (
            f"🎉 *Импорт завершён!*\n\n"
            f"Загружено фото: *{count}*\n"
            f"Охвачено дней: *{len(days)}*\n\n"
            "Теперь все приёмы пищи можно посмотреть в 📅 *Календаре*."
        )

    context.user_data.pop("import_count", None)
    context.user_data.pop("import_days", None)

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb_main_menu())
    return MENU


# ─── Заглушки для непредвиденных сообщений ────────────────────────────────────

async def _hint_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Используйте меню для навигации или введите /menu.",
        reply_markup=kb_main_menu(),
    )
    return MENU


async def _hint_hunger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Выберите цифру голода на кнопках выше (1–10).",
        reply_markup=kb_scale("hunger"),
    )
    return AWAITING_HUNGER


async def _hint_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Отправьте фото тарелки или напишите текстом что ели.\n"
        "Для выхода — /menu",
    )
    return AWAITING_PHOTO


async def _hint_satiety(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Выберите цифру насыщения на кнопках выше (1–10).",
        reply_markup=kb_satiety_with_correct(),
    )
    return AWAITING_SATIETY


async def _hint_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    now = _now()
    await update.message.reply_text(
        "Нажмите на день в календаре или введите /menu.",
        reply_markup=kb_calendar(user_id, now.year, now.month),
    )
    return CALENDAR_VIEW


# ─── Сборка и запуск ──────────────────────────────────────────────────────────

def main() -> None:
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",    cmd_start),
            CommandHandler("menu",     cmd_start),
            CommandHandler("add",      cmd_add),
            CommandHandler("today",    cmd_today),
            CommandHandler("calendar", cmd_calendar),
            CommandHandler("advice",   cmd_advice),
            CommandHandler("import",   cmd_import),
        ],
        states={
            MENU: [
                CallbackQueryHandler(cb_meal_new,       pattern=r"^meal:new$"),
                CallbackQueryHandler(cb_analysis_today, pattern=r"^analysis:today$"),
                CallbackQueryHandler(cb_calendar_open,  pattern=r"^calendar:open$"),
                CallbackQueryHandler(cb_advice_today,   pattern=r"^advice:today$"),
                CallbackQueryHandler(cb_import_start,   pattern=r"^import:start$"),
                CallbackQueryHandler(cb_main_menu,      pattern=r"^menu:main$"),
                MessageHandler(filters.ALL,             _hint_menu),
            ],
            AWAITING_HUNGER: [
                CallbackQueryHandler(cb_hunger, pattern=r"^hunger:\d+$"),
                MessageHandler(filters.ALL,     _hint_hunger),
            ],
            AWAITING_PHOTO: [
                MessageHandler(filters.PHOTO, msg_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_text_meal),
                CallbackQueryHandler(cb_analyze_text_only, pattern=r"^analyze:text_only$"),
                MessageHandler(filters.ALL,   _hint_photo),
            ],
            AWAITING_CLARIFICATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_clarification),
                CallbackQueryHandler(cb_skip_clarification, pattern=r"^skip:clarification$"),
            ],
            AWAITING_SATIETY: [
                CallbackQueryHandler(cb_satiety,          pattern=r"^satiety:\d+$"),
                CallbackQueryHandler(cb_correct_analysis, pattern=r"^correct:analysis$"),
                MessageHandler(filters.ALL,               _hint_satiety),
            ],
            CALENDAR_VIEW: [
                CallbackQueryHandler(cb_calendar_nav,  pattern=r"^cal:(prev|next|day|noop):"),
                CallbackQueryHandler(cb_calendar_open, pattern=r"^calendar:open$"),
                CallbackQueryHandler(cb_meal_detail,   pattern=r"^meal:detail:\d+$"),
                CallbackQueryHandler(cb_cal_back,      pattern=r"^cal:back$"),
                CallbackQueryHandler(cb_main_menu,     pattern=r"^menu:main$"),
                MessageHandler(filters.ALL,            _hint_calendar),
            ],
            BULK_IMPORT: [
                MessageHandler(filters.Document.IMAGE, msg_import_document),
                MessageHandler(filters.PHOTO,          msg_import_photo_warning),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_import_text),
                CallbackQueryHandler(cb_import_save,    pattern=r"^import:save$"),
                CallbackQueryHandler(cb_import_correct, pattern=r"^import:correct$"),
                CallbackQueryHandler(cb_import_discard, pattern=r"^import:discard$"),
                CallbackQueryHandler(cb_import_done,    pattern=r"^import:done$"),
                CallbackQueryHandler(cb_main_menu,      pattern=r"^menu:main$"),
            ],
        },
        fallbacks=[
            CommandHandler("start",    cmd_start),
            CommandHandler("menu",     cmd_start),
            CommandHandler("add",      cmd_add),
            CommandHandler("today",    cmd_today),
            CommandHandler("calendar", cmd_calendar),
            CommandHandler("advice",   cmd_advice),
            CommandHandler("import",   cmd_import),
            # import-кнопки работают из любого состояния
            CallbackQueryHandler(cb_import_save,    pattern=r"^import:save$"),
            CallbackQueryHandler(cb_import_correct, pattern=r"^import:correct$"),
            CallbackQueryHandler(cb_import_discard, pattern=r"^import:discard$"),
            CallbackQueryHandler(cb_import_done,    pattern=r"^import:done$"),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    # Fallback для нажатий на старые кнопки после перезапуска бота
    async def _orphaned_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer(
            "Сессия истекла. Начните заново: нажмите /menu",
            show_alert=True,
        )

    # Не ловим import:* — они обрабатываются в fallbacks ConversationHandler
    app.add_handler(CallbackQueryHandler(
        _orphaned_callback,
        pattern=r"^(?!import:)",
    ))

    # Регистрируем команды в меню Telegram (кнопка "/" в чате)
    async def _set_commands(_app):
        await _app.bot.set_my_commands([
            BotCommand("add",      "🍽 Добавить приём пищи"),
            BotCommand("today",    "📊 Анализ за сегодня"),
            BotCommand("calendar", "📅 Календарь питания"),
            BotCommand("advice",   "💡 Советы на сегодня"),
            BotCommand("import",   "📥 Загрузить архив фото"),
            BotCommand("menu",     "🏠 Главное меню"),
        ])

    app.post_init = _set_commands

    logger.info("Бот запущен. Нажмите Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
