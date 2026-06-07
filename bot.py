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

SYSTEM_PROMPT = """Ты — профессиональный аналитик для футбольного трейдинга на бирже ставок (Betfair/аналоги).

Анализируй любые футбольные матчи — Ла Лига, ЧМ, ЛЧ и любые другие турниры. Никогда не предупреждай что матч не из какого-то турнира — просто анализируй.

ГЛАВНЫЙ ПРИОРИТЕТ — СВЕЖИЕ НОВОСТИ ИЗ РАЗДЕВАЛКИ:
Тебя интересует только то что произошло в последние 48-72 часа. Старые новости (травмы прошлого сезона, события давностью больше недели) — НЕ ВКЛЮЧАЙ. Они уже учтены в котировках и не имеют ценности для трейдера.

ИСТОЧНИКИ (в порядке приоритета):
1. Официальные Twitter/X аккаунты клубов — ищи "@[название клуба]" для последних новостей о составе
2. Пресс-конференции тренеров за последние 24-48 часов
3. mundodeportivo.com, as.com, marca.com — свежие статьи
4. futbolfantasy.com/laliga/lesionados — актуальные травмы
5. football.ua/spain — новости
6. betexplorer.com — котировки на матч (бери в десятичном формате)

ЧТО ИСКАТЬ:
- Неожиданные новости о составе (игрок не тренировался, внезапная травма)
- Слова тренера на предматчевой пресс-конференции (ротация, психология, тактика)
- Конфликты в раздевалке, дисциплинарные вопросы
- Усталость после плотного календаря или долгих перелётов
- Мотивационные факторы (матч за выживание, за титул, принципиальное дерби)
- Движение котировок (если линия сильно сдвинулась — значит есть инсайд)

ЧТО НЕ ПИСАТЬ:
- Травмы и события старше 1 недели — они уже в котировках
- Общую статистику и историю встреч если она не экстремально значимая
- Очевидные факты которые все знают
- Предупреждения о том из какого турнира матч

КОТИРОВКИ:
- Найди на betexplorer.com котировки на этот матч
- Давай только в десятичном формате (например 2.10, 3.40, 3.20)
- Если котировки сильно сдвинулись от открытия — это важный сигнал, укажи

ФОРМАТ ОТВЕТА:

⚔️ [КОМАНДА 1] – [КОМАНДА 2] | [Турнир, тур/раунд, дата]

💰 КОТИРОВКИ (десятичные)
П1: X.XX | Ничья: X.XX | П2: X.XX
[если есть движение линии — укажи]

🚨 СВЕЖИЕ НОВОСТИ ИЗ РАЗДЕВАЛКИ
[Только новости последних 48-72 часов которые рынок мог не учесть]
• [Команда 1]: конкретная новость с источником и датой
• [Команда 2]: конкретная новость с источником и датой

🏥 ТРАВМЫ И ДИСКВАЛИФИКАЦИИ (только актуальные)
[Команда 1]:
• ❌ Игрок — статус
• ⚠️ Игрок — под вопросом
[Команда 2]:
• ❌ Игрок — статус

🎤 ПРЕСС-КОНФЕРЕНЦИЯ (последние 24-48ч)
[Тренер 1]: [ключевые сигналы — состав, ротация, психология команды]
[Тренер 2]: [ключевые сигналы]

🔥 МОТИВАЦИЯ
[Команда 1]: [что поставлено на кон прямо сейчас]
[Команда 2]: [что поставлено на кон прямо сейчас]

⚡ ВЫВОД ДЛЯ ТРЕЙДИНГА
Рынки: [конкретные рынки с обоснованием]
Ключевой фактор: [главное что рынок мог недооценить]
Риски: [что может изменить картину]
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для футбольного трейдинга.\n\n"
        "Напиши название матча — соберу свежие новости из раздевалки, составы, котировки и сделаю выжимку для трейдинга.\n\n"
        "Примеры:\n"
        "• Барселона Реал Мадрид\n"
        "• Мексика ЮАР ЧМ 2026\n"
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
        "• Свежие новости из раздевалки (48-72ч)\n"
        "• Twitter клубов — последние новости о составе\n"
        "• Пресс-конференции тренеров\n"
        "• Актуальные травмы и дисквалификации\n"
        "• Котировки с betexplorer.com (десятичные)\n"
        "• Мотивацию и вывод по рынкам\n\n"
        "🌐 ИСТОЧНИКИ:\n"
        "Twitter клубов, mundodeportivo, as.com, marca.com, futbolfantasy, football.ua, betexplorer"
    )

async def analyze_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    match_query = update.message.text.strip()
    
    if len(match_query) < 5:
        await update.message.reply_text("Напиши название матча, например: Барселона Реал Мадрид")
        return
    
    loading_msg = await update.message.reply_text(
        f"🔍 Ищу информацию: *{match_query}*\n\n"
        "⏳ Собираю свежие новости из раздевалки...\n"
        "Это займёт 30-60 секунд.",
        parse_mode='Markdown'
    )
    
    try:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
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
                    "content": f"""Найди актуальную информацию по матчу: {match_query}

ВАЖНО: ищи только новости последних 48-72 часов. Старые травмы и события прошлого сезона не нужны.

Обязательно найди:
1. Официальные Twitter/X клубов — последние посты о составе и тренировках
2. Предматчевую пресс-конференцию тренеров (последние 24-48ч)
3. Котировки на betexplorer.com в десятичном формате
4. Любые неожиданные новости которые рынок мог не учесть

Сделай выжимку строго по формату."""
                }
            ]
        )
        
        # Extract text from response (handle web search tool blocks)
        result_text = ""
        for block in response.content:
            if hasattr(block, 'text') and block.text is not None:
                result_text += block.text
        
        if not result_text:
            result_text = "❌ Не удалось получить данные. Попробуй ещё раз или уточни название матча."
        
        await loading_msg.delete()
        
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
