"""
Модуль анализа еды через Google Gemini API (мультимодальная модель с vision).
Принципы Гарвардской тарелки:
  • 50% — овощи и фрукты (больше овощей)
  • 25% — цельнозерновые
  • 25% — белок (рыба, птица, бобовые, орехи)
  • Полезные жиры умеренно
  • Вода как основной напиток
"""

import asyncio
import io
import json
import logging
import re
import time
from datetime import datetime

from google import genai
from google.genai import types
from PIL import Image

from config import GEMINI_FALLBACK_MODELS, GEMINI_MODEL, GOOGLE_API_KEY

logger = logging.getLogger(__name__)

_client = genai.Client(api_key=GOOGLE_API_KEY)

# Конфигурация: отключаем thinking для скорости (gemini-2.5-flash думает по умолчанию)
_NO_THINK = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_budget=0)
)

# ─── Промпты ──────────────────────────────────────────────────────────────────

_ANALYZE_PROMPT = """
Ты — диетолог-помощник в приложении «Дневник питания». Перед тобой фотография еды.{extra}

Проанализируй её и верни ТОЛЬКО валидный JSON (без блоков кода, без пояснений) в формате:
{{
  "food_items": ["блюдо 1", "блюдо 2"],
  "description": "Краткое описание тарелки",
  "calories_estimate": 450,
  "proteins_g": 28,
  "fats_g": 14,
  "carbs_g": 52,
  "vegetables_percent": 40,
  "grains_percent": 25,
  "protein_percent": 30,
  "other_percent": 5,
  "harvard_score": 7,
  "harvard_analysis": "Краткий анализ соответствия Гарвардской тарелке",
  "what_missing": ["чего не хватает для идеальной тарелки"],
  "recommendations": ["совет 1", "совет 2"],
  "questions": []
}}

Правила Гарвардской тарелки:
• 50% тарелки — КЛЕТЧАТКА (все овощи и все фрукты относятся ТОЛЬКО сюда, без исключений)
• 25% — МЕДЛЕННЫЕ УГЛЕВОДЫ (цельнозерновые крупы, макароны из твёрдых сортов, картофель, хлеб)
• 25% — БЕЛОК (рыба, птица, мясо, яйца, бобовые, орехи, творог)
• Полезные жиры умеренно (масло, авокадо)
• Вода как основной напиток

Важно: поле vegetables_percent = процент клетчатки (овощи + фрукты вместе), grains_percent = процент медленных углеводов, protein_percent = процент белка.

КРИТИЧЕСКИ ВАЖНО — правила для точного анализа:
1. Оценивай ТОЛЬКО то, что реально видно на фото. Не давай шаблонных советов.
2. Проценты (vegetables_percent, grains_percent, protein_percent) — это визуальная доля каждого компонента от общего объёма тарелки. Если салат занимает половину тарелки — vegetables_percent ≥ 45.
3. what_missing и recommendations — ТОЛЬКО про реально отсутствующее. Если овощей достаточно — не пиши про овощи. Если белка хватает — не советуй добавить белок.
4. recommendations должны быть конкретными и применимыми к данному блюду, а не общими ("ешьте больше овощей" — запрещено, если овощи уже есть в достаточном количестве).
5. harvard_score должен честно отражать реальное соответствие: если тарелка почти идеальна — ставь 8-9, не занижай.

Если блюдо не видно чётко или есть неопределённости — задай уточняющие вопросы в поле "questions".
Все тексты — на русском языке.
"""

_TEXT_ANALYZE_PROMPT = """
Ты — диетолог-помощник в приложении «Дневник питания». Пользователь описал свой приём пищи текстом.

Описание: {description}

Проанализируй и верни ТОЛЬКО валидный JSON (без блоков кода, без пояснений) в формате:
{{
  "food_items": ["блюдо 1", "блюдо 2"],
  "description": "Краткое описание тарелки",
  "calories_estimate": 450,
  "proteins_g": 28,
  "fats_g": 14,
  "carbs_g": 52,
  "vegetables_percent": 40,
  "grains_percent": 25,
  "protein_percent": 30,
  "other_percent": 5,
  "harvard_score": 7,
  "harvard_analysis": "Краткий анализ соответствия Гарвардской тарелке",
  "what_missing": ["чего не хватает для идеальной тарелки"],
  "recommendations": ["совет 1", "совет 2"],
  "questions": []
}}

Правила Гарвардской тарелки:
• 50% тарелки — КЛЕТЧАТКА (все овощи и все фрукты относятся ТОЛЬКО сюда, без исключений)
• 25% — МЕДЛЕННЫЕ УГЛЕВОДЫ (цельнозерновые крупы, макароны из твёрдых сортов, картофель, хлеб)
• 25% — БЕЛОК (рыба, птица, мясо, яйца, бобовые, орехи, творог)
• Полезные жиры умеренно (масло, авокадо)
• Вода как основной напиток

Важно: поле vegetables_percent = процент клетчатки (овощи + фрукты вместе), grains_percent = процент медленных углеводов, protein_percent = процент белка.

КРИТИЧЕСКИ ВАЖНО:
1. Оценивай пропорции по описанию честно. Если сказано "большой салат" — vegetables_percent высокий.
2. what_missing и recommendations — только про реально отсутствующее в описании.
3. Не давай общих советов типа "ешьте больше клетчатки/овощей", если клетчатка уже упомянута в достаточном количестве.

Если описание неполное — задай уточняющие вопросы в поле "questions".
Все тексты — на русском языке.
"""

_CLARIFY_PROMPT = """
Ты — диетолог-помощник. Ранее ты проанализировал приём пищи. Пользователь хочет внести уточнения.

Предыдущий анализ: {previous}
{questions_section}
Комментарий / исправление пользователя: {answers}

Обнови анализ с учётом этой информации. Верни ТОЛЬКО валидный JSON того же формата.
Поле "questions" должно быть [].
Все тексты — на русском языке.
"""

_DAILY_ADVICE_PROMPT = """
Ты — дружелюбный диетолог-помощник. Проанализируй все приёмы пищи пользователя за сегодня
и дай персональные советы.

Данные о приёмах пищи:
{meals}

Структура ответа (обычный текст, не JSON):
1. **Оценка дня** — итоговый балл по Гарвардской тарелке (1–10) и пара слов
2. **Что было хорошо** — конкретные плюсы
3. **Что улучшить** — конкретные минусы
4. **Советы на завтра** — 2–3 практических совета
5. **Паттерны голода/насыщения** — если заметны проблемы (переедание, еда без голода и т.д.)

Тон — мотивирующий и поддерживающий. На русском языке.
"""


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def _parse_json_response(text: str) -> dict:
    """Парсит JSON из ответа модели, убирая возможные обёртки."""
    text = text.strip()
    if "```" in text:
        text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    return json.loads(text)


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc)
    return (
        "429" in msg or "ResourceExhausted" in msg or "quota" in msg.lower() or "RESOURCE_EXHAUSTED" in msg
        or "503" in msg or "UNAVAILABLE" in msg or "high demand" in msg.lower()
    )


def _is_not_found(exc: Exception) -> bool:
    msg = str(exc)
    return "404" in msg or "not found" in msg.lower() or "NOT_FOUND" in msg


def _call_with_retry(fn_factory):
    """Вызывает fn_factory(model_name), перебирая Gemini-модели при 429/404/503.
    Если все модели исчерпали квоту — ждёт 25 секунд и пробует снова один раз."""
    models = [GEMINI_MODEL] + GEMINI_FALLBACK_MODELS

    for attempt in range(2):
        last_exc: Exception | None = None
        for model_name in models:
            try:
                result = fn_factory(model_name)
                if model_name != GEMINI_MODEL:
                    logger.info("Используется резервная модель: %s", model_name)
                return result
            except Exception as exc:
                if _is_rate_limit(exc):
                    logger.warning("Rate limit %s — переключаюсь на следующую модель", model_name)
                    last_exc = exc
                elif _is_not_found(exc):
                    logger.warning("Модель недоступна %s — пропускаю", model_name)
                    last_exc = exc
                else:
                    raise

        if attempt == 0 and last_exc is not None:
            logger.warning("Все модели перегружены — жду 25 сек и повторяю")
            time.sleep(25)

    raise last_exc or RuntimeError("Все Gemini-модели недоступны")


def _image_to_part(image_bytes: bytes) -> types.Part:
    """Конвертирует изображение в JPEG и возвращает Part для Gemini API."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


def _sync_analyze(image_bytes: bytes, description: str | None = None) -> dict:
    extra = (
        f"\n\nПользователь добавил описание: «{description}»\nИспользуй его как подсказку при анализе."
        if description else ""
    )
    prompt = _ANALYZE_PROMPT.format(extra=extra)
    image_part = _image_to_part(image_bytes)

    response = _call_with_retry(
        lambda model: _client.models.generate_content(
            model=model,
            contents=[image_part, prompt],
            config=_NO_THINK,
        )
    )
    text = response.text
    logger.debug("Gemini raw response: %r", text)
    if not text:
        raise ValueError("Модель вернула пустой ответ.")
    return _parse_json_response(text)


def _sync_analyze_text(description: str) -> dict:
    prompt = _TEXT_ANALYZE_PROMPT.format(description=description)
    response = _call_with_retry(
        lambda model: _client.models.generate_content(model=model, contents=prompt, config=_NO_THINK)
    )
    text = response.text
    logger.debug("Gemini text response: %r", text)
    if not text:
        raise ValueError("Модель вернула пустой ответ.")
    return _parse_json_response(text)


def _sync_clarify(previous: dict, questions: list[str], answers: str) -> dict:
    if questions:
        questions_section = "Вопросы которые были заданы:\n" + "\n".join(f"- {q}" for q in questions)
    else:
        questions_section = ""
    prompt = _CLARIFY_PROMPT.format(
        previous=json.dumps(previous, ensure_ascii=False),
        questions_section=questions_section,
        answers=answers,
    )
    response = _call_with_retry(
        lambda model: _client.models.generate_content(model=model, contents=prompt, config=_NO_THINK)
    )
    return _parse_json_response(response.text)


def _sync_daily_advice(meals_data: list[dict]) -> str:
    lines = []
    for i, m in enumerate(meals_data, 1):
        lines.append(f"Приём {i} ({m.get('meal_time', '?')}):")
        lines.append(f"  Блюда: {', '.join(m.get('food_items', [])) or 'не указано'}")
        lines.append(f"  Голод до: {m.get('hunger_before', '?')}/10")
        lines.append(f"  Насыщение после: {m.get('satiety_after', '?')}/10")
        lines.append(f"  Калории: ~{m.get('calories_estimate', '?')} ккал")
        lines.append(f"  Оценка тарелки: {m.get('harvard_score', '?')}/10")
        lines.append(f"  Овощи/фрукты: {m.get('vegetables_percent', '?')}%")
        lines.append(f"  Злаки: {m.get('grains_percent', '?')}%")
        lines.append(f"  Белок: {m.get('protein_percent', '?')}%")
        lines.append("")
    prompt = _DAILY_ADVICE_PROMPT.format(meals="\n".join(lines))
    response = _call_with_retry(
        lambda model: _client.models.generate_content(model=model, contents=prompt, config=_NO_THINK)
    )
    return response.text


# ─── Публичные async-функции ──────────────────────────────────────────────────

def get_photo_datetime(image_bytes: bytes) -> datetime | None:
    """Извлекает дату и время съёмки из EXIF-метаданных фото."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif = img.getexif()
        for tag_id in (36867, 36868, 306):
            val = exif.get(tag_id)
            if val:
                return datetime.strptime(val.strip(), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


async def analyze_food_photo(image_bytes: bytes, description: str | None = None) -> dict:
    """Анализирует фото еды, опционально с текстовым описанием."""
    return await asyncio.to_thread(_sync_analyze, image_bytes, description)


async def analyze_food_text(description: str) -> dict:
    """Анализирует текстовое описание еды. Возвращает структурированный словарь."""
    return await asyncio.to_thread(_sync_analyze_text, description)


async def refine_analysis(previous: dict, questions: list[str], answers: str) -> dict:
    """Уточняет анализ на основе ответов пользователя."""
    return await asyncio.to_thread(_sync_clarify, previous, questions, answers)


async def get_daily_advice(meals_data: list[dict]) -> str:
    """Генерирует персональные советы по итогам дня."""
    return await asyncio.to_thread(_sync_daily_advice, meals_data)
