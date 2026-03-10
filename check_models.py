"""
Запустите этот скрипт чтобы увидеть какие модели Gemini
доступны для вашего API-ключа.
"""
from google import genai
from config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

print("Доступные модели:\n")
for m in client.models.list():
    print(f"  {m.name}")
