
# 시드는 INSERT OR IGNORE 라서 이미 있는 ID의 값은 바뀌지 않는다.
# - CSV 값을 고친 뒤 반영하려면 finguard.db 를 지우고 이 파일을 다시 실행한다.
# - 시연 전에 AI 요약을 비우려면: UPDATE consultations SET ai_summary = NULL;
import csv
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "finguard.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"
CUSTOMERS_CSV_PATH = BASE_DIR / "seed_customers.csv"
TRANSACTIONS_CSV_PATH = BASE_DIR / "seed_transactions.csv"
CONSULTATIONS_CSV_PATH = BASE_DIR / "seed_consultations.csv"

CUSTOMER_FIELDS = ("customer_id", "name", "phone", "card_last4")
TRANSACTION_FIELDS = ("transaction_id", "customer_id", "amount",
                      "country", "transaction_at", "status")
CONSULTATION_FIELDS = ("consultation_id", "customer_id", "conversation",
                       "ai_summary", "category", "created_at")


def load_csv_rows(csv_path, fields):
    # CSV 헤더 순서와 무관하게 fields 순서의 튜플 목록으로 반환 (빈 칸은 NULL)
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        return [tuple(row[field] or None for field in fields)
                for row in csv.DictReader(f)]


def initialize_database(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)

    try:
        conn.execute("PRAGMA foreign_keys = ON")

        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        # 고객 10명, 거래 50건 (기존 C001~C003, T001~T003 포함)
        customers = load_csv_rows(CUSTOMERS_CSV_PATH, CUSTOMER_FIELDS)
        transactions = load_csv_rows(TRANSACTIONS_CSV_PATH,
                                     TRANSACTION_FIELDS)

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

        # 상담 예시 10건
        conn.executemany(
            """
            INSERT OR IGNORE INTO consultations
            (consultation_id, customer_id, conversation,
             ai_summary, category, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            load_csv_rows(CONSULTATIONS_CSV_PATH, CONSULTATION_FIELDS)
        )

        conn.commit()
        print("FinGuard AI 데이터베이스 초기화 성공!")

    finally:
        conn.close()


if __name__ == "__main__":
    initialize_database()
