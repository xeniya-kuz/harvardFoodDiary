import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
TIMEZONE: ZoneInfo = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))

# Основная и резервные модели Gemini (все поддерживают vision, бесплатно)
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_FALLBACK_MODELS = [
    "gemini-2.5-pro",         # резерв 1
    "gemini-2.0-flash",       # резерв 2 (стабильнее при высокой нагрузке)
]

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан в файле .env")
if not GOOGLE_API_KEY:
    raise ValueError("GEMINI_API_KEY не задан в файле .env")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан в файле .env")
