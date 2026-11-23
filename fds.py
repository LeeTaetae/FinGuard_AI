import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "database" / "finguard.db"

# 위험 점수 (규칙별 가산점)
SCORE_OVERSEAS = 30
SCORE_AMOUNT_SPIKE = 30
SCORE_LATE_NIGHT = 20
SCORE_REPEATED = 20

# 규칙 기준값
HOME_COUNTRY = "KR"
AMOUNT_SPIKE_RATIO = 5          # 고객의 다른 거래 평균 대비 배수
LATE_NIGHT_START_HOUR = 0       # 00:00 이상
LATE_NIGHT_END_HOUR = 6         # 06:00 미만
REPEAT_WINDOW_MINUTES = 10      # 기준 거래 ±N분 이내
REPEAT_MIN_COUNT = 3            # 기준 거래 포함 N건 이상이면 반복

# 위험 등급 기준 (점수 이상)
LEVEL_HIGH_MIN = 60
LEVEL_MEDIUM_MIN = 30

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _risk_level(score):
    if score >= LEVEL_HIGH_MIN:
        return "HIGH"
    if score >= LEVEL_MEDIUM_MIN:
        return "MEDIUM"
    return "LOW"


def _score_transaction(tx, others):
    """tx: 대상 거래(dict), others: 같은 고객의 다른 거래 목록."""
    score = 0
    reasons = []
    tx_time = datetime.strptime(tx["transaction_at"], DATETIME_FORMAT)

    if tx["country"] != HOME_COUNTRY:
        score += SCORE_OVERSEAS
        reasons.append(f"해외 결제({tx['country']})")

    if others:
        average = sum(o["amount"] for o in others) / len(others)
        if average > 0 and tx["amount"] >= average * AMOUNT_SPIKE_RATIO:
            score += SCORE_AMOUNT_SPIKE
            reasons.append(
                f"평소 평균({average:,.0f}원)의 "
                f"{tx['amount'] / average:.1f}배 결제"
            )

    if LATE_NIGHT_START_HOUR <= tx_time.hour < LATE_NIGHT_END_HOUR:
        score += SCORE_LATE_NIGHT
        reasons.append(f"심야 결제({tx_time:%H:%M})")

    window = timedelta(minutes=REPEAT_WINDOW_MINUTES)
    nearby = sum(
        1 for o in others
        if abs(datetime.strptime(o["transaction_at"], DATETIME_FORMAT)
               - tx_time) <= window
    )
    if nearby + 1 >= REPEAT_MIN_COUNT:
        score += SCORE_REPEATED
        reasons.append(
            f"{REPEAT_WINDOW_MINUTES}분 내 반복 결제({nearby + 1}건)"
        )

    return {
        "transaction_id": tx["transaction_id"],
        "risk_score": score,
        "risk_level": _risk_level(score),
        "reasons": reasons,
    }


def _fetch_transactions(customer_id, db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT transaction_id, customer_id, amount,
                   country, transaction_at
            FROM transactions
            WHERE customer_id = ?
            ORDER BY transaction_at
            """,
            (customer_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def analyze_customer(customer_id, db_path=DEFAULT_DB_PATH):
    """고객의 모든 거래를 점검해 가장 위험한 거래의 결과를 반환한다.

    반환: {transaction_id, risk_score, risk_level, reasons}
    거래가 없으면 transaction_id=None, 점수 0(LOW).
    """
    transactions = _fetch_transactions(customer_id, db_path)
    if not transactions:
        return {"transaction_id": None, "risk_score": 0,
                "risk_level": "LOW", "reasons": []}

    results = [
        _score_transaction(tx, [o for o in transactions if o is not tx])
        for tx in transactions
    ]
    return max(results, key=lambda r: r["risk_score"])
