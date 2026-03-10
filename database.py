import json
from datetime import date, datetime
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from config import DATABASE_URL


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id          SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username    TEXT,
                    first_name  TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS meals (
                    id                  SERIAL PRIMARY KEY,
                    user_id             BIGINT NOT NULL,
                    photo_file_id       TEXT,
                    meal_time           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hunger_before       INTEGER,
                    satiety_after       INTEGER,
                    food_items          TEXT,
                    description         TEXT,
                    calories_estimate   INTEGER,
                    proteins_g          REAL,
                    fats_g              REAL,
                    carbs_g             REAL,
                    harvard_score       INTEGER,
                    harvard_analysis    TEXT,
                    vegetables_percent  INTEGER,
                    grains_percent      INTEGER,
                    protein_percent     INTEGER,
                    what_missing        TEXT,
                    ai_questions        TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id)
                )
            """)


# ─── Users ────────────────────────────────────────────────────────────────────

def get_or_create_user(telegram_id: int, username: str | None, first_name: str | None) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO users (telegram_id, username, first_name) VALUES (%s, %s, %s)",
                    (telegram_id, username, first_name),
                )


# ─── Meals ────────────────────────────────────────────────────────────────────

def save_meal(user_id: int, data: dict) -> int:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meals (
                    user_id, photo_file_id, meal_time, hunger_before,
                    food_items, description, calories_estimate,
                    proteins_g, fats_g, carbs_g,
                    harvard_score, harvard_analysis,
                    vegetables_percent, grains_percent, protein_percent,
                    what_missing, ai_questions
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    user_id,
                    data.get("photo_file_id"),
                    data.get("meal_time", datetime.now().isoformat()),
                    data.get("hunger_before"),
                    json.dumps(data.get("food_items", []), ensure_ascii=False),
                    data.get("description"),
                    data.get("calories_estimate"),
                    data.get("proteins_g"),
                    data.get("fats_g"),
                    data.get("carbs_g"),
                    data.get("harvard_score"),
                    data.get("harvard_analysis"),
                    data.get("vegetables_percent"),
                    data.get("grains_percent"),
                    data.get("protein_percent"),
                    json.dumps(data.get("what_missing", []), ensure_ascii=False),
                    json.dumps(data.get("ai_questions", []), ensure_ascii=False),
                ),
            )
            return cur.fetchone()[0]


def update_satiety(meal_id: int, satiety: int) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE meals SET satiety_after = %s WHERE id = %s",
                (satiety, meal_id),
            )


def get_meals_for_date(user_id: int, target_date: date) -> list:
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM meals
                WHERE user_id = %s AND meal_time::date = %s
                ORDER BY meal_time
                """,
                (user_id, target_date.isoformat()),
            )
            return [dict(r) for r in cur.fetchall()]


def get_meal_days_in_month(user_id: int, year: int, month: int) -> set[int]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT EXTRACT(DAY FROM meal_time)::INTEGER AS day
                FROM meals
                WHERE user_id = %s
                  AND EXTRACT(YEAR FROM meal_time) = %s
                  AND EXTRACT(MONTH FROM meal_time) = %s
                """,
                (user_id, year, month),
            )
            return {r[0] for r in cur.fetchall()}


def get_meal_by_id(meal_id: int) -> dict | None:
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM meals WHERE id = %s", (meal_id,))
            row = cur.fetchone()
            return dict(row) if row else None
