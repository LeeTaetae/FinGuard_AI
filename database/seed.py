
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "finguard.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def initialize_database():
    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        customers = [
            ("C001", "김민수", "010-0000-0001", "1234"),
            ("C002", "이서연", "010-0000-0002", "5678"),
            ("C003", "박지훈", "010-0000-0003", "9012"),
        ]

        transactions = [
            ("T001", "C001", 8000, "KR",
             "2026-11-23 14:00:00", "COMPLETED"),
            ("T002", "C001", 1200000, "US",
             "2026-11-23 03:00:00", "COMPLETED"),
            ("T003", "C002", 35000, "KR",
             "2026-11-23 18:00:00", "COMPLETED"),
        ]

        conn.executemany(
            """
            INSERT OR IGNORE INTO customers
            (customer_id, name, phone, card_last4)
            VALUES (?, ?, ?, ?)
            """,
            customers
        )

        conn.executemany(
            """
            INSERT OR IGNORE INTO transactions
            (transaction_id, customer_id, amount,
             country, transaction_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            transactions
        )

        conn.commit()
        print("FinGuard AI 데이터베이스 초기화 성공!")

    finally:
        conn.close()


if __name__ == "__main__":
    initialize_database()
