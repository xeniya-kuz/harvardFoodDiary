import sqlite3
import json
from datetime import date, datetime
from contextlib import contextmanager
from config import DB_PATH


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE NOT NULL,
                username    TEXT,
                first_name  TEXT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS meals (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                photo_file_id       TEXT,
                meal_time           DATETIME DEFAULT CURRENT_TIMESTAMP,
                hunger_before       INTEGER,
                satiety_after       INTEGER,
                food_items          TEXT,       -- JSON-массив распознанных блюд
                description         TEXT,       -- Текстовое описание
                calories_estimate   INTEGER,
                proteins_g          REAL,
                fats_g              REAL,
                carbs_g             REAL,
                harvard_score       INTEGER,    -- 1–10
                harvard_analysis    TEXT,
                vegetables_percent  INTEGER,
                grains_percent      INTEGER,
                protein_percent     INTEGER,
                what_missing        TEXT,       -- JSON-массив
                ai_questions        TEXT,       -- JSON-массив вопросов ИИ
                FOREIGN KEY (user_id) REFERENCES users(telegram_id)
            );
        """)


# ─── Users ────────────────────────────────────────────────────────────────────

def get_or_create_user(telegram_id: int, username: str | None, first_name: str | None) -> None:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)",
                (telegram_id, username, first_name),
            )


# ─── Meals ────────────────────────────────────────────────────────────────────

def save_meal(user_id: int, data: dict) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO meals (
                user_id, photo_file_id, meal_time, hunger_before,
                food_items, description, calories_estimate,
                proteins_g, fats_g, carbs_g,
                harvard_score, harvard_analysis,
                vegetables_percent, grains_percent, protein_percent,
                what_missing, ai_questions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return cursor.lastrowid


def update_satiety(meal_id: int, satiety: int) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE meals SET satiety_after = ? WHERE id = ?",
            (satiety, meal_id),
        )


def get_meals_for_date(user_id: int, target_date: date) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM meals
            WHERE user_id = ? AND DATE(meal_time) = ?
            ORDER BY meal_time
            """,
            (user_id, target_date.isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]


def get_meal_days_in_month(user_id: int, year: int, month: int) -> set[int]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT CAST(strftime('%d', meal_time) AS INTEGER) AS day
            FROM meals
            WHERE user_id = ?
              AND strftime('%Y', meal_time) = ?
              AND strftime('%m', meal_time) = ?
            """,
            (user_id, str(year), f"{month:02d}"),
        ).fetchall()
        return {r["day"] for r in rows}


def get_meal_by_id(meal_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
        return dict(row) if row else None
