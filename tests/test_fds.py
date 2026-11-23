import sqlite3

import pytest

import fds
import seed
from database.category_labels import CATEGORY_LABELS, category_label

# 고객별 기대 결과: (risk_level, 사유 키워드)
EXPECTED = {
    "C001": ("HIGH", ["해외", "배", "심야"]),   # 시연 시나리오 T002
    "C002": ("LOW", []),
    "C003": ("LOW", []),
    "C004": ("LOW", []),
    "C005": ("MEDIUM", ["해외"]),
    "C006": ("LOW", ["심야"]),
    "C007": ("MEDIUM", ["해외", "반복"]),
    "C008": ("MEDIUM", ["배"]),
    "C009": ("HIGH", ["해외", "배", "심야"]),
    "C010": ("LOW", []),
}


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "finguard_test.db"
    seed.initialize_database(path)
    return path


def test_seed_counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        transactions = conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0]
    finally:
        conn.close()
    assert customers == 10
    assert transactions == 50


def test_seed_consultations(db_path):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT category, COUNT(*) FROM consultations GROUP BY category"
        ).fetchall()
    finally:
        conn.close()
    assert sum(count for _, count in rows) == 10
    assert {category for category, _ in rows} == {
        "LOST_CARD", "UNAUTHORIZED_TRANSACTION", "PAYMENT_INQUIRY"}


def test_seed_ai_summary_is_empty(db_path):
    conn = sqlite3.connect(db_path)
    try:
        filled = conn.execute(
            "SELECT COUNT(*) FROM consultations WHERE ai_summary IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert filled == 0


def test_category_labels_cover_all_codes():
    codes = {"LOST_CARD", "UNAUTHORIZED_TRANSACTION", "PAYMENT_INQUIRY",
             "GENERAL_INQUIRY", "OTHER"}
    assert set(CATEGORY_LABELS) == codes
    assert category_label("LOST_CARD") == "카드 분실"
    assert category_label("UNKNOWN") == "UNKNOWN"


def test_seed_is_idempotent(db_path):
    seed.initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions").fetchone()[0] == 50
    finally:
        conn.close()


def test_demo_scenario_t002(db_path):
    result = fds.analyze_customer("C001", db_path)
    assert result["transaction_id"] == "T002"
    assert result["risk_score"] == (
        fds.SCORE_OVERSEAS + fds.SCORE_AMOUNT_SPIKE + fds.SCORE_LATE_NIGHT
    )
    assert result["risk_level"] == "HIGH"


@pytest.mark.parametrize("customer_id", sorted(EXPECTED))
def test_risk_level_per_customer(db_path, customer_id):
    level, keywords = EXPECTED[customer_id]
    result = fds.analyze_customer(customer_id, db_path)
    assert result["risk_level"] == level
    joined = " ".join(result["reasons"])
    for keyword in keywords:
        assert keyword in joined


def test_result_shape(db_path):
    result = fds.analyze_customer("C002", db_path)
    assert {"risk_score", "risk_level", "reasons"} <= set(result)
    assert isinstance(result["reasons"], list)


def test_normal_and_suspicious_are_separated(db_path):
    levels = {c: fds.analyze_customer(c, db_path)["risk_level"]
              for c in EXPECTED}
    assert {c for c, lv in levels.items() if lv == "LOW"}
    assert {c for c, lv in levels.items() if lv != "LOW"}


def test_unknown_customer_is_low(db_path):
    result = fds.analyze_customer("C999", db_path)
    assert result["risk_score"] == 0
    assert result["risk_level"] == "LOW"
    assert result["reasons"] == []


def test_analyze_transaction_uses_only_past(db_path):
    # T002(03:00)는 C001의 과거 3건(평균 8,000원)만 보고 평가된다
    result = fds.analyze_transaction("T002", db_path)
    assert result["risk_score"] == 80
    assert result["risk_level"] == "HIGH"
    # T001(14:00)의 과거에는 03:00의 고액 거래(T002)가 있지만 자신은 정상
    assert fds.analyze_transaction("T001", db_path)["risk_score"] == 0


def test_future_transaction_does_not_change_past_score(tmp_path):
    path = tmp_path / "past_only.db"
    seed.initialize_database(path)
    before = fds.analyze_transaction("T013", path)["risk_score"]
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO transactions VALUES "
            "('TX99', 'C004', 9000000, 'KR', '2026-12-31 12:00:00', 'COMPLETED')"
        )
        conn.commit()
    finally:
        conn.close()
    assert fds.analyze_transaction("T013", path)["risk_score"] == before


def test_earlier_big_transaction_does_not_hide_the_next_one(tmp_path):
    # 평균을 쓰면 첫 고액 거래가 평균을 끌어올려 두 번째가 0점이 되던 경우
    path = tmp_path / "median.db"
    seed.initialize_database(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("INSERT INTO transactions VALUES "
                     "('TXA', 'C002', 900000, 'KR', '2026-11-24 14:00:00', 'COMPLETED')")
        conn.execute("INSERT INTO transactions VALUES "
                     "('TXB', 'C002', 900000, 'KR', '2026-11-24 15:00:00', 'COMPLETED')")
        conn.commit()
    finally:
        conn.close()
    assert fds.analyze_transaction("TXA", path)["risk_score"] == fds.SCORE_AMOUNT_SPIKE
    assert fds.analyze_transaction("TXB", path)["risk_score"] == fds.SCORE_AMOUNT_SPIKE
