
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from ai.consultation_analyzer import Category, analyze_consultation


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
# 모의 CRM (사고 접수) - UiPath는 템플릿의 고정 HTML ID를 사용
# ==========================================

CATEGORY_LABELS = {
    Category.LOST_CARD.value: "카드 분실",
    Category.UNAUTHORIZED_TRANSACTION.value: "카드 부정사용 의심",
    Category.PAYMENT_INQUIRY.value: "결제 문의",
    Category.GENERAL_INQUIRY.value: "일반 문의",
    Category.OTHER.value: "기타",
}
RISK_LEVELS = {"LOW": "낮음", "MEDIUM": "보통", "HIGH": "높음"}
STATUS_LABELS = {"RECEIVED": "접수", "IN_PROGRESS": "처리중", "DONE": "처리완료"}


@app.get("/crm/incidents/new")
def crm_incident_form():
    return render_template(
        "crm/incident_new.html",
        categories=CATEGORY_LABELS, risk_levels=RISK_LEVELS,
        form={}, error=None,
    )


@app.post("/crm/incidents")
def crm_incident_create():
    form = {k: request.form.get(k, "").strip() for k in
            ("request_id", "customer_id", "category", "summary", "risk_level")}

    def fail(message):
        return render_template(
            "crm/incident_new.html",
            categories=CATEGORY_LABELS, risk_levels=RISK_LEVELS,
            form=form, error=message,
        ), 400

    if form["category"] not in CATEGORY_LABELS:
        return fail("사고 유형을 선택해 주세요.")
    if not form["summary"] or len(form["summary"]) > 2000:
        return fail("상담 요약을 1~2000자로 입력해 주세요.")
    if form["risk_level"] and form["risk_level"] not in RISK_LEVELS:
        return fail("위험도 값이 올바르지 않습니다.")

    conn = get_db_connection()
    try:
        if not conn.execute("SELECT 1 FROM customers WHERE customer_id = ?",
                            (form["customer_id"],)).fetchone():
            return fail("존재하지 않는 고객 ID입니다.")

        if form["request_id"]:
            existing = conn.execute(
                "SELECT incident_id FROM incidents WHERE request_id = ?",
                (form["request_id"],)).fetchone()
            if existing:  # 재실행 시 새로 만들지 않고 기존 접수번호를 돌려준다
                return redirect(url_for("crm_incident_detail",
                                        incident_id=existing["incident_id"], dup=1))

        incident_id = f"INC-{datetime.now():%Y%m%d}-{uuid4().hex[:6].upper()}"
        conn.execute(
            """
            INSERT INTO incidents
            (incident_id, request_id, customer_id, category, summary, risk_level)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (incident_id, form["request_id"] or None, form["customer_id"],
             form["category"], form["summary"], form["risk_level"] or None),
        )
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("crm_incident_detail", incident_id=incident_id))


INCIDENT_SELECT = """
    SELECT i.*, c.name AS customer_name
    FROM incidents i JOIN customers c ON i.customer_id = c.customer_id
"""


@app.get("/crm/incidents/<incident_id>")
def crm_incident_detail(incident_id):
    conn = get_db_connection()
    try:
        row = conn.execute(INCIDENT_SELECT + " WHERE i.incident_id = ?",
                           (incident_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        abort(404)
    return render_template(
        "crm/incident_detail.html", incident=row,
        duplicate=request.args.get("dup") == "1",
        categories=CATEGORY_LABELS, statuses=STATUS_LABELS, risk_levels=RISK_LEVELS,
    )


@app.get("/crm/incidents")
def crm_incident_list():
    conn = get_db_connection()
    try:
        rows = conn.execute(INCIDENT_SELECT + " ORDER BY i.created_at DESC").fetchall()
    finally:
        conn.close()
    return render_template(
        "crm/incident_list.html", incidents=rows,
        categories=CATEGORY_LABELS, statuses=STATUS_LABELS, risk_levels=RISK_LEVELS,
    )


# ==========================================
# Flask 실행
# ==========================================

if __name__ == "__main__":
    app.run(debug=True, port=5000)
