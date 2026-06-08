import os
import logging
import sqlite3
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
CHAT_ID = os.environ.get("CHAT_ID", "")

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
            notified_24h INTEGER DEFAULT 0,
            notified_6h INTEGER DEFAULT 0,
            notified_1h INTEGER DEFAULT 0,
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
# SYSTEM PROMPTS
# ─────────────────────────────────────────
ANALYSIS_PROMPT = """Ты — профессиональный аналитик для футбольного трейдинга на бирже ставок (Betfair/аналоги).

Анализируй любые футбольные матчи — Ла Лига, Серия А, ЧМ, ЛЧ и любые другие турниры.

ГЛАВНЫЙ ПРИОРИТЕТ — СВЕЖИЕ НОВОСТИ ИЗ РАЗДЕВАЛКИ:
Только новости последних 48-72 часов. Старые травмы и события прошлого сезона — НЕ ВКЛЮЧАЙ.

ИСТОЧНИКИ (в порядке приоритета):
1. Официальные Twitter/X аккаунты клубов
2. Пресс-конференции тренеров за последние 24-48 часов
3. mundodeportivo.com, as.com, marca.com, gazzetta.it, corrieredellosport.it
4. futbolfantasy.com/laliga/lesionados
5. football.ua/spain
6. betexplorer.com — котировки в десятичном формате

ФОРМАТ ОТВЕТА:

⚔️ [КОМАНДА 1] – [КОМАНДА 2] | [Турнир, дата]

💰 КОТИРОВКИ (десятичные)
П1: X.XX | Ничья: X.XX | П2: X.XX

🚨 СВЕЖИЕ НОВОСТИ ИЗ РАЗДЕВАЛКИ
• [Команда 1]: новость (источник, дата)
• [Команда 2]: новость (источник, дата)

🏥 ТРАВМЫ И ДИСКВАЛИФИКАЦИИ
[Команда 1]:
• ❌ Игрок — статус
• ⚠️ Игрок — под вопросом
[Команда 2]:
• ❌ Игрок — статус

🎤 ПРЕСС-КОНФЕРЕНЦИЯ (последние 24-48ч)
[Тренер 1]: ключевые сигналы
[Тренер 2]: ключевые сигналы

🔥 МОТИВАЦИЯ
[Команда 1]: что поставлено на кон
[Команда 2]: что поставлено на кон

⚡ ВЫВОД ДЛЯ ТРЕЙДИНГА
Рынки: конкретные рынки с обоснованием
Ключевой фактор: что рынок мог недооценить
Риски: что может изменить картину"""

MONITOR_PROMPT = """Ты — аналитик для футбольного трейдинга. Проверяешь есть ли НОВЫЕ важные новости по матчу за последние 4 часа.

ИЩИ ТОЛЬКО:
- Новые травмы или возвращения из травм
- Дисквалификации
- Неожиданные заявления тренера
- Конфликты в раздевалке
- Резкое движение котировок
- Официальные объявления клуба

НЕ СООБЩАЙ если нет ничего нового за последние 4 часа.

ФОРМАТ если есть новости:
🔔 [МАТЧ] | ОБНОВЛЕНИЕ

[новость 1 с источником и временем]
[новость 2 с источником и временем]

ФОРМАТ если нет ничего нового:
НИЧЕГО_НОВОГО"""

LINEUP_PROMPT = """Ты — аналитик для футбольного трейдинга. Найди официальный стартовый состав или заявку на матч.

ФОРМАТ если нашёл:
🚨 [МАТЧ] | СОСТАВ ОБЪЯВЛЕН

[Команда 1] (схема):
Вратарь: ...
Защита: ...
Полузащита: ...
Атака: ...
❌ Отсутствуют: ...
⚠️ Неожиданно в составе/вне состава: ...

[Команда 2] (схема):
...

⚡ ВАЖНО ДЛЯ ТРЕЙДИНГА: [что меняет в котировках]

ФОРМАТ если не нашёл:
СОСТАВ_НЕ_ОБЪЯВЛЕН"""

PRESSCONF_PROMPT = """Ты — аналитик для футбольного трейдинга. Найди пресс-конференции тренеров за последние 24 часа.

ФОРМАТ если нашёл:
📢 [МАТЧ] | ПРЕСС-КОНФЕРЕНЦИЯ

[Тренер 1 — имя]:
• [ключевая цитата или сигнал]
• [ротация/состав/психология]

[Тренер 2 — имя]:
• [ключевая цитата или сигнал]

⚡ ВАЖНО ДЛЯ ТРЕЙДИНГА: [что меняет в котировках]

ФОРМАТ если не нашёл:
ПРЕССУХА_НЕ_НАЙДЕНА"""

# ─────────────────────────────────────────
# CLAUDE API CALL
# ─────────────────────────────────────────
def call_claude(system_prompt, user_message, max_tokens=3000):
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
# TELEGRAM HANDLERS
# ─────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для футбольного трейдинга.\n\n"
        "Напиши название матча — сделаю анализ и поставлю на автомониторинг.\n\n"
        "Примеры:\n"
        "• Барселона Реал Мадрид\n"
        "• Мексика ЮАР ЧМ 2026\n"
        "• Милан Ювентус\n\n"
        "Команды:\n"
        "/list — матчи на мониторинге\n"
        "/stop [номер] — снять с мониторинга\n"
        "/help — помощь\n\n"
        "⏳ Анализ занимает 30-60 секунд."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 КАК ПОЛЬЗОВАТЬСЯ:\n\n"
        "1. Напиши название матча\n"
        "2. Получи анализ + матч встаёт на мониторинг\n"
        "3. Бот сам присылает пуши:\n\n"
        "⏰ РАСПИСАНИЕ ПУШЕЙ:\n"
        "• Каждые 4ч — новые травмы/скандалы\n"
        "• За 24ч до матча — пресс-конференции\n"
        "• За 6ч до матча — заявка на матч\n"
        "• За 1ч до матча — стартовый состав\n\n"
        "📌 ТУРНИРЫ:\n"
        "Ла Лига, Серия А, ЧМ-2026, ЛЧ и любые другие\n\n"
        "🌐 ИСТОЧНИКИ:\n"
        "Twitter клубов, mundodeportivo, as.com, marca.com, gazzetta.it, betexplorer"
    )

async def list_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat_id)
    matches = get_matches(chat_id)
    
    if not matches:
        await update.message.reply_text("📋 Нет матчей на мониторинге.\n\nНапиши название матча чтобы добавить.")
        return
    
    text = "📋 МАТЧИ НА МОНИТОРИНГЕ:\n\n"
    for m in matches:
        match_id = m[0]
        match_name = m[1]
        match_date = m[2] or "дата не указана"
        added = m[4][:10] if m[4] else "?"
        text += f"{match_id}. {match_name}\n"
        text += f"   📅 {match_date} | добавлен {added}\n\n"
    
    text += "Чтобы снять с мониторинга: /stop [номер]"
    await update.message.reply_text(text)

async def stop_monitoring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat_id)
    args = context.args
    
    if not args:
        await update.message.reply_text("Укажи номер матча: /stop 1\n\nПосмотреть список: /list")
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
            await update.message.reply_text("Матч не найден. Проверь номер через /list")
            return
        
        remove_match(match_id)
        await update.message.reply_text(f"✅ Матч '{row[0]}' снят с мониторинга.")
    except ValueError:
        await update.message.reply_text("Укажи номер: /stop 1")

async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match_query = update.message.text.strip()
    chat_id = str(update.message.chat_id)
    
    if len(match_query) < 5:
        await update.message.reply_text("Напиши название матча, например: Барселона Реал Мадрид")
        return
    
    loading_msg = await update.message.reply_text(
        f"🔍 Анализирую: *{match_query}*\n\n"
        "⏳ Собираю свежие новости...\n"
        "Это займёт 30-60 секунд.",
        parse_mode='Markdown'
    )
    
    result = call_claude(
        ANALYSIS_PROMPT,
        f"""Найди актуальную информацию по матчу: {match_query}

ВАЖНО: только новости последних 48-72 часов.

Обязательно найди:
1. Twitter/X клубов — последние посты о составе
2. Пресс-конференцию тренеров (последние 24-48ч)
3. Котировки на betexplorer.com в десятичном формате
4. Дату и время матча

Сделай выжимку строго по формату."""
    )
    
    await loading_msg.delete()
    
    if not result:
        await update.message.reply_text("❌ Ошибка при сборе данных. Попробуй ещё раз.")
        return
    
    # Split if too long
    if len(result) > 4000:
        parts = [result[i:i+4000] for i in range(0, len(result), 4000)]
        for part in parts:
            await update.message.reply_text(part)
    else:
        await update.message.reply_text(result)
    
    # Add to monitoring
    added = add_match(match_query, None, chat_id)
    if added:
        await update.message.reply_text(
            f"✅ *{match_query}* добавлен на мониторинг\n\n"
            "Буду присылать автоматически:\n"
            "• Каждые 4ч — новые травмы/скандалы\n"
            "• За 24ч — пресс-конференции\n"
            "• За 6ч — заявка на матч\n"
            "• За 1ч — стартовый состав\n\n"
            "Список мониторинга: /list\n"
            "Снять с мониторинга: /stop [номер]",
            parse_mode='Markdown'
        )

# ─────────────────────────────────────────
# BACKGROUND MONITORING
# ─────────────────────────────────────────
async def check_news_update(app):
    """Every 4 hours - check for new injuries/news"""
    matches = get_all_matches()
    if not matches:
        return
    
    logger.info(f"Checking news for {len(matches)} matches...")
    
    for match in matches:
        match_id = match[0]
        match_name = match[1]
        chat_id = match[3]
        last_hash = match[9] or ""
        
        result = call_claude(
            MONITOR_PROMPT,
            f"""Проверь есть ли НОВЫЕ важные новости за последние 4 часа по матчу: {match_name}
            
Ищи: новые травмы, дисквалификации, заявления тренеров, движение котировок.
Только реально новое — не то что уже было известно."""
        )
        
        if not result:
            continue
            
        if "НИЧЕГО_НОВОГО" in result:
            logger.info(f"No news for {match_name}")
            continue
        
        # Check if this is actually new (simple hash)
        import hashlib
        news_hash = hashlib.md5(result.encode()).hexdigest()[:8]
        
        if news_hash == last_hash:
            continue
            
        update_match(match_id, "last_news_hash", news_hash)
        update_match(match_id, "last_check", datetime.now().isoformat())
        
        try:
            await app.bot.send_message(chat_id=chat_id, text=result)
        except Exception as e:
            logger.error(f"Error sending message: {e}")

async def check_pressconf(app):
    """24 hours before match - check press conferences"""
    matches = get_all_matches()
    
    for match in matches:
        match_id = match[0]
        match_name = match[1]
        chat_id = match[3]
        notified = match[6]  # notified_24h
        
        if notified:
            continue
        
        result = call_claude(
            PRESSCONF_PROMPT,
            f"""Найди пресс-конференции тренеров за последние 24 часа по матчу: {match_name}
            
Этот матч должен состояться в ближайшие 24-48 часов."""
        )
        
        if not result or "ПРЕССУХА_НЕ_НАЙДЕНА" in result:
            continue
            
        update_match(match_id, "notified_24h", 1)
        
        try:
            await app.bot.send_message(chat_id=chat_id, text=result)
        except Exception as e:
            logger.error(f"Error sending message: {e}")

async def check_lineup(app):
    """1-6 hours before match - check lineups"""
    matches = get_all_matches()
    
    for match in matches:
        match_id = match[0]
        match_name = match[1]
        chat_id = match[3]
        notified_6h = match[7]
        notified_1h = match[8]
        
        result = call_claude(
            LINEUP_PROMPT,
            f"""Найди официальный стартовый состав или заявку на матч: {match_name}
            
Матч должен состояться сегодня или завтра."""
        )
        
        if not result or "СОСТАВ_НЕ_ОБЪЯВЛЕН" in result:
            continue
        
        if not notified_1h:
            update_match(match_id, "notified_1h", 1)
            try:
                await app.bot.send_message(chat_id=chat_id, text=result)
            except Exception as e:
                logger.error(f"Error sending message: {e}")

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
def main():
    init_db()
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("list", list_matches))
    app.add_handler(CommandHandler("stop", stop_monitoring))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_match))
    
    # Scheduler
    scheduler = AsyncIOScheduler()
    
    # Every 4 hours - check for news
    scheduler.add_job(
        check_news_update, 
        'interval', 
        hours=4,
        args=[app],
        id='news_check'
    )
    
    # Every 2 hours - check for lineups/pressconf
    scheduler.add_job(
        check_pressconf,
        'interval',
        hours=2,
        args=[app],
        id='pressconf_check'
    )
    
    scheduler.add_job(
        check_lineup,
        'interval',
        hours=1,
        args=[app],
        id='lineup_check'
    )
    
    scheduler.start()
    logger.info("Bot started with monitoring!")
    
    app.run_polling(allowed_updates=["message"])

if __name__ == "__main__":
    main()
