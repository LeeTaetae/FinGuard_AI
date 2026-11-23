
import sqlite3
from pathlib import Path
from flask import Flask, jsonify

app = Flask(__name__)
app.json.ensure_ascii = False

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database" / "finguard.db"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@app.get("/")
def index():
    return jsonify({
        "project": "FinGuard AI",
        "status": "running",
        "message": "금융 콜센터 AI 비서 서버 실행 성공"
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


# 전체 고객 조회
@app.get("/api/customers")
def get_customers():
    conn = get_db_connection()

    try:
        rows = conn.execute(
            "SELECT * FROM customers ORDER BY customer_id"
        ).fetchall()

        return jsonify([dict(row) for row in rows])

    finally:
        conn.close()


# 전체 거래 내역 조회
@app.get("/api/transactions")
def get_transactions():
    conn = get_db_connection()

    try:
        rows = conn.execute(
            """
            SELECT
                t.transaction_id,
                t.customer_id,
                c.name AS customer_name,
                t.amount,
                t.country,
                t.transaction_at,
                t.status
            FROM transactions t
            JOIN customers c
              ON t.customer_id = c.customer_id
            ORDER BY t.transaction_at DESC
            """
        ).fetchall()

        return jsonify([dict(row) for row in rows])

    finally:
        conn.close()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
