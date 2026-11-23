
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from ai.consultation_analyzer import analyze_consultation


app = Flask(__name__)
app.json.ensure_ascii = False

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database" / "finguard.db"


# ==========================================
# API Rate Limit 설정
# ==========================================

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.errorhandler(429)
def rate_limit_exceeded(error):
    return jsonify({
        "error": "요청 횟수를 초과했습니다. 잠시 후 다시 시도해 주세요."
    }), 429


# ==========================================
# Database
# ==========================================

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ==========================================
# 기본 API
# ==========================================

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


# ==========================================
# 고객 조회 API
# ==========================================

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


# ==========================================
# 거래 내역 조회 API
# ==========================================

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


# ==========================================
# AI 상담 분석 API
# ==========================================

@app.post("/api/ai/analyze")
@limiter.limit("5 per minute")
def analyze_ai():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "JSON 요청 본문이 필요합니다."
        }), 400

    conversation = data.get("conversation")

    if not isinstance(conversation, str) or not conversation.strip():
        return jsonify({
            "error": "상담 내용을 입력해 주세요."
        }), 400

    if len(conversation) > 10000:
        return jsonify({
            "error": "상담 내용은 10000자 이하로 입력해 주세요."
        }), 400

    try:
        result = analyze_consultation(conversation)

        return jsonify(result), 200

    except Exception:
        app.logger.exception("AI 상담 분석 실패")

        return jsonify({
            "error": "AI 상담 분석 중 오류가 발생했습니다."
        }), 502


# ==========================================
# Flask 실행
# ==========================================

if __name__ == "__main__":
    app.run(debug=True, port=5000)
