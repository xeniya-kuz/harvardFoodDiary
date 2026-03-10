"""
Тест API-ключа OpenRouter — текстовый запрос без изображения.
"""
from openai import OpenAI
from config import OPENROUTER_API_KEY, OPENROUTER_MODEL

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

print(f"Ключ: {OPENROUTER_API_KEY[:15]}...")
print(f"Модель: {OPENROUTER_MODEL}")
print("Отправляю тестовый запрос...\n")

try:
    response = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[{"role": "user", "content": "Скажи 'Привет' одним словом."}],
    )
    print("✅ Успех! Ответ:", response.choices[0].message.content)
except Exception as e:
    print(f"❌ Ошибка: {e}")
