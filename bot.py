import os
import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Clients
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# Priority sources
PRIORITY_SOURCES = [
    "futbolfantasy.com/laliga/lesionados",
    "mundodeportivo.com",
    "football.ua/spain",
    "as.com",
    "marca.com",
]

SYSTEM_PROMPT = """Ты — профессиональный аналитик для футбольного трейдинга на бирже ставок (Betfair/аналоги).

Твоя задача: по названию матча Ла Лиги найти через веб-поиск актуальную информацию и сделать выжимку ТОЛЬКО того, что может повлиять на котировки.

ПРИОРИТЕТНЫЕ ИСТОЧНИКИ (ищи в первую очередь):
- futbolfantasy.com/laliga/lesionados (травмы)
- mundodeportivo.com
- football.ua/spain
- as.com
- marca.com

ТАКЖЕ ИЩИ: свежие новости из любых испанских спортивных СМИ за последние 48 часов.

ФОРМАТ ОТВЕТА (строго):

⚔️ [КОМАНДА 1] – [КОМАНДА 2] | Тур XX

🏥 ТРАВМЫ И ДИСКВАЛИФИКАЦИИ
[Команда 1]:
• ❌ Игрок — причина
• ⚠️ Игрок — под вопросом
[Команда 2]:
• ❌ Игрок — причина

🔥 МОТИВАЦИЯ
[Команда 1]: [1-2 предложения — турнирная ситуация, что поставлено на кон]
[Команда 2]: [1-2 предложения]

🎤 ПРЕСС-КОНФЕРЕНЦИИ / ИНТЕРВЬЮ
[Тренер 1]: [только важные сигналы — ротация, тактика, психология]
[Тренер 2]: [только важные сигналы]

📊 ФОРМА (последние 5 матчей)
[Команда 1]: W/D/L серия
[Команда 2]: W/D/L серия

⚡ ВЫВОД ДЛЯ ТРЕЙДИНГА
Рынки: [конкретные рынки — АГ, тоталы, карточки, победитель]
Ключевой фактор: [главное что влияет на котировки]
Риски: [что может изменить картину]

ВАЖНЫЕ ПРАВИЛА:
- Только факты которые реально влияют на исход или котировки
- Никакой воды и общих фраз
- Если информации нет — пиши "Данных нет"
- Всегда проверяй актуальность (дата публикации)
- Если тренер объявил ротацию — это КРИТИЧНО, выдели отдельно
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для трейдинга по Ла Лиге.\n\n"
        "Просто напиши название матча и я соберу всю важную информацию:\n\n"
        "Примеры:\n"
        "• Барселона Реал Мадрид\n"
        "• Севилья Бетис\n"
        "• Атлетико Валенсия\n\n"
        "⏳ Сбор данных занимает 30-60 секунд."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 КАК ПОЛЬЗОВАТЬСЯ:\n\n"
        "1. Напиши название матча\n"
        "2. Жди 30-60 секунд\n"
        "3. Получи выжимку для трейдинга\n\n"
        "📌 ЧТО АНАЛИЗИРУЮ:\n"
        "• Травмы и дисквалификации\n"
        "• Мотивацию обеих команд\n"
        "• Пресс-конференции тренеров\n"
        "• Форму последних матчей\n"
        "• Вывод по рынкам\n\n"
        "🌐 ИСТОЧНИКИ:\n"
        "futbolfantasy, mundodeportivo, football.ua, as.com, marca.com + актуальный поиск"
    )

async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match_query = update.message.text.strip()
    
    # Ignore very short messages
    if len(match_query) < 5:
        await update.message.reply_text("Напиши название матча, например: Барселона Реал Мадрид")
        return
    
    # Send loading message
    loading_msg = await update.message.reply_text(
        f"🔍 Ищу информацию по матчу: *{match_query}*\n\n"
        "⏳ Собираю данные из источников...\n"
        "Это займёт 30-60 секунд.",
        parse_mode='Markdown'
    )
    
    try:
        # Call Claude API with web search
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search"
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"""Найди актуальную информацию по матчу Ла Лиги: {match_query}

Ищи на испанском и русском языках.
Приоритет: futbolfantasy.com, mundodeportivo.com, football.ua, as.com, marca.com
Также используй любые свежие источники за последние 48 часов.

Сделай выжимку строго по формату — только то что влияет на котировки."""
                }
            ]
        )
        
        # Extract text from response
        result_text = ""
        for block in response.content:
            if hasattr(block, 'text'):
                result_text += block.text
        
        if not result_text:
            result_text = "❌ Не удалось получить данные. Попробуй ещё раз или уточни название матча."
        
        # Delete loading message
        await loading_msg.delete()
        
        # Send result (split if too long)
        if len(result_text) > 4000:
            parts = [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]
            for part in parts:
                await update.message.reply_text(part)
        else:
            await update.message.reply_text(result_text)
            
    except Exception as e:
        logger.error(f"Error analyzing match: {e}")
        await loading_msg.edit_text(
            "❌ Произошла ошибка при сборе данных.\n"
            "Попробуй ещё раз через минуту."
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_match))
    
    logger.info("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
