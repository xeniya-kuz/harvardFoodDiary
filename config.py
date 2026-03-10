import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
GOOGLE_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
DB_PATH: str = os.getenv("DB_PATH", "food_diary.db")

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
