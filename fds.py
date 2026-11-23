import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "database" / "finguard.db"

# 위험 점수 (규칙별 가산점)
SCORE_OVERSEAS = 30
SCORE_AMOUNT_SPIKE = 30
SCORE_LATE_NIGHT = 20
SCORE_REPEATED = 20

# 규칙 기준값
HOME_COUNTRY = "KR"
AMOUNT_SPIKE_RATIO = 5          # 과거 거래 금액 중앙값 대비 배수
MIN_HISTORY_FOR_SPIKE = 3       # 중앙값을 낼 최소 과거 거래 수 (미만이면 5배 판정 안 함)
LATE_NIGHT_START_HOUR = 0       # 00:00 이상
LATE_NIGHT_END_HOUR = 6         # 06:00 미만
REPEAT_WINDOW_MINUTES = 10      # 기준 거래 직전 N분 이내
REPEAT_MIN_COUNT = 3            # 기준 거래 포함 N건 이상이면 반복

# 위험 등급 기준 (점수 이상)
LEVEL_HIGH_MIN = 60
LEVEL_MEDIUM_MIN = 30

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

_TRANSACTION_COLUMNS = (
    "transaction_id, customer_id, amount, country, transaction_at"
)


def _risk_level(score):
    if score >= LEVEL_HIGH_MIN:
        return "HIGH"
    if score >= LEVEL_MEDIUM_MIN:
        return "MEDIUM"
    return "LOW"


def _score_transaction(tx, history):
    """tx: 대상 거래(dict), history: 같은 고객의 '대상 거래 이전' 거래 목록.

    history에는 대상 거래보다 나중에 일어난 거래를 넣지 않는다.
    """
    score = 0
    reasons = []
    tx_time = datetime.strptime(tx["transaction_at"], DATETIME_FORMAT)

    if tx["country"] != HOME_COUNTRY:
        score += SCORE_OVERSEAS
        reasons.append(f"해외 결제({tx['country']})")

    if len(history) >= MIN_HISTORY_FOR_SPIKE:
        # 평균은 앞선 고액 거래 한두 건에 끌려 올라가므로 중앙값을 쓴다
        typical = median(h["amount"] for h in history)
        if typical > 0 and tx["amount"] >= typical * AMOUNT_SPIKE_RATIO:
            score += SCORE_AMOUNT_SPIKE
            reasons.append(
                f"평소 중앙값({typical:,.0f}원)의 "
                f"{tx['amount'] / typical:.1f}배 결제"
            )

    if LATE_NIGHT_START_HOUR <= tx_time.hour < LATE_NIGHT_END_HOUR:
        score += SCORE_LATE_NIGHT
        reasons.append(f"심야 결제({tx_time:%H:%M})")

    window = timedelta(minutes=REPEAT_WINDOW_MINUTES)
    recent = sum(
        1 for h in history
        if timedelta(0)
        <= tx_time - datetime.strptime(h["transaction_at"], DATETIME_FORMAT)
        <= window
    )
    if recent + 1 >= REPEAT_MIN_COUNT:
        score += SCORE_REPEATED
        reasons.append(
            f"{REPEAT_WINDOW_MINUTES}분 내 반복 결제({recent + 1}건)"
        )

    return {
        "transaction_id": tx["transaction_id"],
        "risk_score": score,
        "risk_level": _risk_level(score),
        "reasons": reasons,
    }


def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def analyze_transaction(transaction_id, db_path=DEFAULT_DB_PATH):
    """거래 1건을 '그 거래 이전의 과거 거래'만으로 평가한다 (실시간 판단용).

    과거 거래 = 같은 고객의 거래 중 (거래 시각, 거래 ID) 순서에서
    대상 거래보다 앞선 것.
    반환: {transaction_id, risk_score, risk_level, reasons}
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT {_TRANSACTION_COLUMNS} FROM transactions "
            "WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        tx = dict(row)

        history = [
            dict(r) for r in conn.execute(
                f"""
                SELECT {_TRANSACTION_COLUMNS} FROM transactions
                WHERE customer_id = ?
                  AND (transaction_at < ?
                       OR (transaction_at = ? AND transaction_id < ?))
                ORDER BY transaction_at, transaction_id
                """,
                (tx["customer_id"],
                 tx["transaction_at"], tx["transaction_at"],
                 tx["transaction_id"]),
            ).fetchall()
        ]
    finally:
        conn.close()

    return _score_transaction(tx, history)


def analyze_customer(customer_id, db_path=DEFAULT_DB_PATH):
    """고객의 모든 거래를 각각 '그 시점까지의 과거 거래'로 평가해
    가장 위험한 거래의 결과를 반환한다 (analyze_transaction과 같은 기준).

    반환: {transaction_id, risk_score, risk_level, reasons}
    거래가 없으면 transaction_id=None, 점수 0(LOW).
    """
    conn = _connect(db_path)
    try:
        transactions = [
            dict(r) for r in conn.execute(
                f"""
                SELECT {_TRANSACTION_COLUMNS} FROM transactions
                WHERE customer_id = ?
                ORDER BY transaction_at, transaction_id
                """,
                (customer_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    if not transactions:
        return {"transaction_id": None, "risk_score": 0,
                "risk_level": "LOW", "reasons": []}

    results = [
        _score_transaction(tx, transactions[:i])
        for i, tx in enumerate(transactions)
    ]
    return max(results, key=lambda r: r["risk_score"])
