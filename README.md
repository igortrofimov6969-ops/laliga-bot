# 🤖 La Liga Trading Bot

Telegram бот для трейдинга по Ла Лиге. Собирает актуальные новости по матчу и делает выжимку для трейдинга.

## Что умеет
- Ищет травмы и дисквалификации
- Анализирует мотивацию команд
- Парсит пресс-конференции тренеров
- Оценивает форму команд
- Даёт вывод по рынкам для трейдинга

## Источники
- futbolfantasy.com
- mundodeportivo.com
- football.ua
- as.com
- marca.com
- + актуальный веб-поиск

## Деплой на Railway

1. Зайди на railway.app
2. New Project → Deploy from GitHub repo
3. Загрузи эти файлы в GitHub репозиторий
4. В Railway: Settings → Variables → добавь:
   - ANTHROPIC_API_KEY = твой ключ от Anthropic
   - TELEGRAM_BOT_TOKEN = твой токен от BotFather
5. Deploy!

## Использование

Просто напиши боту название матча:
- `Барселона Реал Мадрид`
- `Севилья Бетис`
- `Атлетико Валенсия`
