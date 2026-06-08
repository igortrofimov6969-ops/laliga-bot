import os
import logging
import sqlite3
import hashlib
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import anthropic

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# ─────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("matches.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS monitored_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_name TEXT NOT NULL,
            match_date TEXT,
            chat_id TEXT NOT NULL,
            added_at TEXT NOT NULL,
            last_check TEXT,
            notified_final INTEGER DEFAULT 0,
            last_news_hash TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()

def add_match(match_name, match_date, chat_id):
    conn = sqlite3.connect("matches.db")
    c = conn.cursor()
    c.execute("SELECT id FROM monitored_matches WHERE match_name=? AND chat_id=?",
              (match_name, chat_id))
    if c.fetchone():
        conn.close()
        return False
    c.execute("""
        INSERT INTO monitored_matches
        (match_name, match_date, chat_id, added_at)
        VALUES (?, ?, ?, ?)
    """, (match_name, match_date, chat_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return True

def get_matches(chat_id):
    conn = sqlite3.connect("matches.db")
    c = conn.cursor()
    c.execute("SELECT * FROM monitored_matches WHERE chat_id=?", (chat_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_matches():
    conn = sqlite3.connect("matches.db")
    c = conn.cursor()
    c.execute("SELECT * FROM monitored_matches")
    rows = c.fetchall()
    conn.close()
    return rows

def remove_match(match_id):
    conn = sqlite3.connect("matches.db")
    c = conn.cursor()
    c.execute("DELETE FROM monitored_matches WHERE id=?", (match_id,))
    conn.commit()
    conn.close()

def update_match(match_id, field, value):
    conn = sqlite3.connect("matches.db")
    c = conn.cursor()
    c.execute(f"UPDATE monitored_matches SET {field}=? WHERE id=?", (value, match_id))
    conn.commit()
    conn.close()

# ─────────────────────────────────────────
# SCHEDULE LOGIC
# ─────────────────────────────────────────
def get_hours_until(match_date_str):
    if not match_date_str:
        return None
    try:
        match_date = datetime.fromisoformat(match_date_str)
        return (match_date - datetime.now()).total_seconds() / 3600
    except:
        return None

def should_check_now(match):
    """
    Schedule:
    - 5+ days: once per day (every 24h)
    - 1-5 days: every 8h
    - < 1 day: every 8h (same)
    - < 1h: FINAL REPORT (one time)
    Returns: True / False / 'FINAL' / 'REMOVE'
    """
    match_date_str = match[2]
    last_check_str = match[5]
    notified_final = match[6]

    hours_until = get_hours_until(match_date_str)

    # No date — check every 8h
    if hours_until is None:
        interval_h = 8
    elif hours_until < -2:
        return "REMOVE"  # match ended 2h+ ago
    elif hours_until < 0:
        return False  # match started, wait to remove
    elif hours_until <= 1:
        if not notified_final:
            return "FINAL"
        return False
    elif hours_until <= 120:  # up to 5 days
        interval_h = 8
    else:
        interval_h = 24  # 5+ days

    if not last_check_str:
        return True

    try:
        last_check = datetime.fromisoformat(last_check_str)
        hours_since = (datetime.now() - last_check).total_seconds() / 3600
        return hours_since >= interval_h
    except:
        return True

# ─────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────
ANALYSIS_PROMPT = """Ты — профессиональный аналитик для футбольного трейдинга на бирже ставок (Betfair).

Анализируй любые матчи — Ла Лига, Серия А, ЧМ, ЛЧ и другие турниры.

ПРИОРИТЕТ — СВЕЖИЕ НОВОСТИ (последние 48-72 часа):
Старые травмы и события прошлых сезонов — НЕ ВКЛЮЧАЙ. Они уже в котировках.

ИСТОЧНИКИ:
1. Twitter/X официальные аккаунты клубов
2. Пресс-конференции тренеров (последние 24-48ч)
3. mundodeportivo.com, as.com, marca.com, gazzetta.it, corrieredellosport.it
4. futbolfantasy.com/laliga/lesionados, football.ua/spain
5. betexplorer.com — котировки ТОЛЬКО в десятичном формате

ФОРМАТ:

⚔️ [КОМАНДА 1] – [КОМАНДА 2] | [Турнир | Дата и время]

💰 КОТИРОВКИ (десятичные)
П1: X.XX | Ничья: X.XX | П2: X.XX
[движение линии если есть]

🚨 НОВОСТИ ИЗ РАЗДЕВАЛКИ (последние 48-72ч)
• [Команда 1]: новость — источник, дата
• [Команда 2]: новость — источник, дата

🏥 ТРАВМЫ / ДИСКВАЛИФИКАЦИИ (только актуальные)
[Команда 1]: ❌ игрок — причина | ⚠️ игрок — под вопросом
[Команда 2]: ❌ игрок — причина

🎤 ПРЕСС-КОНФЕРЕНЦИЯ
[Тренер 1]: ключевые слова о составе/тактике/психологии
[Тренер 2]: ключевые слова

🔥 МОТИВАЦИЯ
[Команда 1]: что поставлено на кон
[Команда 2]: что поставлено на кон

⚡ ВЫВОД ДЛЯ ТРЕЙДИНГА
Рынки: [конкретные рынки с обоснованием]
Ключевой фактор: [что рынок мог недооценить]
Риски: [что может изменить картину]"""

MONITOR_PROMPT = """Ты — аналитик для футбольного трейдинга. Ищи ТОЛЬКО новые важные новости за последние несколько часов.

ЧТО ВАЖНО:
- Новая травма или возвращение из травмы
- Дисквалификация
- Неожиданное заявление тренера
- Конфликт в раздевалке
- Резкое движение котировок (betexplorer.com)
- Официальный пост клуба в Twitter/X

ЕСЛИ ЕСТЬ ЧТО-ТО НОВОЕ — отвечай строго в этом формате:
🔔 [МАТЧ] | ОБНОВЛЕНИЕ

• [новость 1] — [источник]
• [новость 2] — [источник]

⚡ Влияние на котировки: [как это меняет рынок]

ЕСЛИ НИЧЕГО НОВОГО — отвечай ТОЛЬКО одним словом: НИЧЕГО"""

FINAL_REPORT_PROMPT = """Ты — аналитик для футбольного трейдинга. Матч начинается через ~1 час. Составь ФИНАЛЬНЫЙ отчёт.

НАЙДИ ОБЯЗАТЕЛЬНО:
1. Официальный стартовый состав (Twitter клубов, официальный сайт)
2. Все последние новости за последние 3 часа
3. Актуальные котировки на betexplorer.com в десятичном формате

ФОРМАТ:

🚨 [МАТЧ] | ФИНАЛЬНЫЙ ОТЧЁТ — ЗА 1 ЧАС

💰 КОТИРОВКИ СЕЙЧАС (десятичные)
П1: X.XX | Ничья: X.XX | П2: X.XX

📋 СТАРТОВЫЕ СОСТАВЫ
[Команда 1] — схема:
Вратарь: ...
Защита: ...
Полузащита: ...
Атака: ...

[Команда 2] — схема:
Вратарь: ...
Защита: ...
Полузащита: ...
Атака: ...

⚠️ СЮРПРИЗЫ В СОСТАВЕ
[неожиданные включения или отсутствия]

🚨 ПОСЛЕДНИЕ НОВОСТИ (последние 3ч)
[если есть что-то важное]

⚡ ФИНАЛЬНЫЙ ВЫВОД ДЛЯ ТРЕЙДИНГА
Рынок: [конкретно что играть]
Обоснование: [почему]
Риск: [что может изменить картину]"""

# ─────────────────────────────────────────
# CLAUDE API
# ─────────────────────────────────────────
def call_claude(system_prompt, user_message, max_tokens=2000):
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system_prompt,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_message}]
        )
        result = ""
        for block in response.content:
            if hasattr(block, 'text') and block.text is not None:
                result += block.text
        return result.strip()
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return None

# ─────────────────────────────────────────
# PARSE DATE
# ─────────────────────────────────────────
def extract_date_from_query(query):
    months = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
        'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
        'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
    }
    words = query.lower().split()
    for i, word in enumerate(words):
        if word in months and i > 0:
            try:
                day = int(words[i-1])
                month = months[word]
                year = datetime.now().year
                dt = datetime(year, month, day, 20, 0)
                clean = ' '.join(w for w in query.split()
                                 if w.lower() != word and w != words[i-1])
                return dt.isoformat(), clean.strip()
            except:
                pass
    return None, query

# ─────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Бот для футбольного трейдинга.\n\n"
        "Напиши матч — получи анализ + автомониторинг.\n\n"
        "📅 Укажи дату для умного расписания:\n"
        "• Мексика ЮАР 15 июня\n"
        "• Испания Германия 24 июня\n"
        "• Милан Ювентус\n\n"
        "⏰ Расписание пушей:\n"
        "5+ дней — раз в сутки\n"
        "До 5 дней — каждые 8ч\n"
        "За 1ч до матча — финальный отчёт со стартовым составом\n\n"
        "/list — матчи на мониторинге\n"
        "/stop [номер] — снять с мониторинга"
    )

async def list_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat_id)
    matches = get_matches(chat_id)

    if not matches:
        await update.message.reply_text(
            "📋 Нет матчей на мониторинге.\n\nНапиши название матча чтобы добавить.")
        return

    text = "📋 МАТЧИ НА МОНИТОРИНГЕ:\n\n"
    for m in matches:
        match_id, match_name, match_date = m[0], m[1], m[2]
        hours_until = get_hours_until(match_date)

        if hours_until is not None:
            if hours_until > 0:
                days = int(hours_until // 24)
                hrs = int(hours_until % 24)
                time_str = f"через {days}д {hrs}ч" if days > 0 else f"через {hrs}ч"
                interval = "раз в сутки" if hours_until > 120 else "каждые 8ч"
            else:
                time_str = "матч идёт/прошёл"
                interval = "—"
        else:
            time_str = "дата не указана"
            interval = "каждые 8ч"

        final_done = "✅" if m[6] else "⏳"
        text += f"{match_id}. {match_name}\n"
        text += f"   📅 {time_str} | 🔄 {interval}\n"
        text += f"   Финальный отчёт: {final_done}\n\n"

    text += "Снять: /stop [номер]"
    await update.message.reply_text(text)

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat_id)
    args = context.args

    if not args:
        await update.message.reply_text("Укажи номер: /stop 1\n\nСписок: /list")
        return

    try:
        match_id = int(args[0])
        conn = sqlite3.connect("matches.db")
        c = conn.cursor()
        c.execute("SELECT match_name FROM monitored_matches WHERE id=? AND chat_id=?",
                  (match_id, chat_id))
        row = c.fetchone()
        conn.close()

        if not row:
            await update.message.reply_text("Матч не найден. Проверь: /list")
            return

        remove_match(match_id)
        await update.message.reply_text(f"✅ '{row[0]}' снят с мониторинга.")
    except ValueError:
        await update.message.reply_text("Укажи номер: /stop 1")

async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    chat_id = str(update.message.chat_id)

    if len(query) < 5:
        await update.message.reply_text(
            "Напиши название матча, например: Барселона Реал Мадрид")
        return

    match_date, clean_query = extract_date_from_query(query)

    loading_msg = await update.message.reply_text(
        f"🔍 Анализирую: *{clean_query}*\n\n"
        "⏳ Собираю свежие новости...\n"
        "30-60 секунд.",
        parse_mode='Markdown'
    )

    result = call_claude(
        ANALYSIS_PROMPT,
        f"""Найди актуальную информацию по матчу: {clean_query}

ВАЖНО: только новости последних 48-72 часов.

Найди:
1. Twitter/X клубов — последние посты о составе
2. Пресс-конференцию тренеров (последние 24-48ч)
3. Котировки на betexplorer.com (десятичные)
4. Точную дату и время матча

Строго по формату.""",
        max_tokens=3000
    )

    await loading_msg.delete()

    if not result:
        await update.message.reply_text(
            "❌ Ошибка при сборе данных. Попробуй ещё раз.")
        return

    if len(result) > 4000:
        parts = [result[i:i+4000] for i in range(0, len(result), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(result)

    added = add_match(clean_query, match_date, chat_id)
    if added:
        hours_until = get_hours_until(match_date)

        if hours_until and hours_until > 0:
            days = int(hours_until // 24)
            hrs = int(hours_until % 24)
            time_str = f"через {days}д {hrs}ч" if days > 0 else f"через {hrs}ч"
            interval_str = "раз в сутки" if hours_until > 120 else "каждые 8ч"
        else:
            time_str = "дата не указана"
            interval_str = "каждые 8ч"

        await update.message.reply_text(
            f"✅ *{clean_query}* добавлен на мониторинг\n\n"
            f"📅 Матч: {time_str}\n"
            f"🔄 Проверка: {interval_str}\n"
            f"🚨 Финальный отчёт: за 1ч до матча\n\n"
            "/list — все матчи\n"
            "/stop [номер] — снять",
            parse_mode='Markdown'
        )

# ─────────────────────────────────────────
# MONITORING LOOP
# ─────────────────────────────────────────
async def run_monitoring(app):
    matches = get_all_matches()
    if not matches:
        return

    logger.info(f"Monitoring: checking {len(matches)} matches")

    for match in matches:
        match_id = match[0]
        match_name = match[1]
        chat_id = match[3]
        last_hash = match[7] or ""

        status = should_check_now(match)

        # Remove finished match
        if status == "REMOVE":
            remove_match(match_id)
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ *{match_name}* — матч завершён, снят с мониторинга.",
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Error: {e}")
            continue

        if status is False:
            continue

        # FINAL REPORT — 1 hour before match
        if status == "FINAL":
            logger.info(f"Sending final report for {match_name}")
            result = call_claude(
                FINAL_REPORT_PROMPT,
                f"""Матч начинается через ~1 час: {match_name}

Найди:
1. Официальный стартовый состав (Twitter клубов, официальный сайт)
2. Последние новости за последние 3 часа
3. Актуальные котировки на betexplorer.com (десятичные)

Финальный отчёт строго по формату.""",
                max_tokens=2500
            )
            if result:
                update_match(match_id, "notified_final", 1)
                update_match(match_id, "last_check", datetime.now().isoformat())
                try:
                    if len(result) > 4000:
                        parts = [result[i:i+4000] for i in range(0, len(result), 4000)]
                        for part in parts:
                            await app.bot.send_message(chat_id=chat_id, text=part)
                    else:
                        await app.bot.send_message(chat_id=chat_id, text=result)
                except Exception as e:
                    logger.error(f"Error: {e}")
            continue

        # REGULAR NEWS CHECK
        if status is True:
            logger.info(f"Regular check for {match_name}")
            result = call_claude(
                MONITOR_PROMPT,
                f"Проверь новые важные новости за последние несколько часов: {match_name}",
                max_tokens=1000
            )

            update_match(match_id, "last_check", datetime.now().isoformat())

            if not result or "НИЧЕГО" in result.upper():
                logger.info(f"No news for {match_name}")
                continue

            news_hash = hashlib.md5(result.encode()).hexdigest()[:8]
            if news_hash == last_hash:
                logger.info(f"Duplicate news for {match_name}, skip")
                continue

            update_match(match_id, "last_news_hash", news_hash)

            try:
                await app.bot.send_message(chat_id=chat_id, text=result)
            except Exception as e:
                logger.error(f"Error: {e}")

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("list", list_matches))
    app.add_handler(CommandHandler("stop", stop_monitoring))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_match))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_monitoring,
        'interval',
        minutes=30,
        args=[app],
        id='smart_monitor'
    )
    scheduler.start()

    logger.info("Bot started!")
    app.run_polling(allowed_updates=["message"])

if __name__ == "__main__":
    main()
