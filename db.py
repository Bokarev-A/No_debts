# db.py
import sqlite3
from pathlib import Path
from datetime import date

DB_PATH = Path("payments.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            amount REAL NOT NULL,
            day_of_month INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()
    conn.close()


def get_or_create_user(tg_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    if row:
        conn.close()
        return row["id"]

    cur.execute("INSERT INTO users (tg_id) VALUES (?)", (tg_id,))
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return user_id


def add_payment(user_id: int, title: str, amount: float, day_of_month: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payments (user_id, title, amount, day_of_month)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, title, amount, day_of_month),
    )
    conn.commit()
    conn.close()


def get_payments_for_user(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM payments WHERE user_id = ? AND active = 1 ORDER BY day_of_month",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_month_total_for_user(user_id: int) -> float:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT SUM(amount) as total FROM payments WHERE user_id = ? AND active = 1",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row["total"] or 0.0


def get_remaining_total_for_user(user_id: int, today: date | None = None) -> float:
    if today is None:
        today = date.today()
    day = today.day

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT SUM(amount) as total
        FROM payments
        WHERE user_id = ? AND active = 1 AND day_of_month >= ?
        """,
        (user_id, day),
    )
    row = cur.fetchone()
    conn.close()
    return row["total"] or 0.0


def get_payments_for_day(day: int):
    """
    Платежи для напоминаний: все платежи с таким day_of_month.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.*, u.tg_id
        FROM payments p
        JOIN users u ON p.user_id = u.id
        WHERE p.active = 1 AND p.day_of_month = ?
        """,
        (day,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows

def get_payment_by_id(user_id: int, payment_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM payments
        WHERE id = ? AND user_id = ? AND active = 1
        """,
        (payment_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row


def delete_payment(user_id: int, payment_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM payments
        WHERE id = ? AND user_id = ?
        """,
        (payment_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted



def update_payment(user_id: int, payment_id: int, title: str, amount: float, day_of_month: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE payments
        SET title = ?, amount = ?, day_of_month = ?
        WHERE id = ? AND user_id = ? AND active = 1
        """,
        (title, amount, day_of_month, payment_id, user_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated

def cleanup_inactive_payments() -> int:
    """
    Удаляет из таблицы payments все записи с active = 0.
    Возвращает количество удалённых записей.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM payments
        WHERE active = 0
        """
    )
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted
