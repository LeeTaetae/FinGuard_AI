import sqlite3

import pytest

import fds
import seed

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
        "카드 분실", "카드 부정사용 의심", "일반 결제 문의"}


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
