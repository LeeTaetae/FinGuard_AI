import sqlite3
import uuid

import pytest

import app as app_module
from database import seed


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(seed, "DB_PATH", db)
    monkeypatch.setattr(app_module, "DB_PATH", db)
    seed.initialize_database(db)
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as client:
        yield client


VALID = {
    "request_id": "REQ-001",
    "customer_id": "C001",
    "category": "UNAUTHORIZED_TRANSACTION",
    "summary": "미승인 해외 결제 문의",
    "risk_level": "HIGH",
}


def test_form_has_fixed_ids(client):
    html = client.get("/crm/incidents/new").get_data(as_text=True)
    for element_id in ("request_id", "customer_id", "category",
                       "risk_level", "summary", "submit-incident"):
        assert f'id="{element_id}"' in html


def test_create_then_list(client):
    res = client.post("/crm/incidents", data=VALID, follow_redirects=True)
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert 'id="incident-id">INC-' in html

    listing = client.get("/crm/incidents").get_data(as_text=True)
    assert "REQ-001" in listing and "카드 부정사용 의심" in listing and "높음" in listing


def test_manual_create_without_request_id(client):
    data = {**VALID, "request_id": "", "risk_level": ""}
    assert client.post("/crm/incidents", data=data).status_code == 302
    assert client.post("/crm/incidents", data=data).status_code == 302  # 수동은 중복 허용


def test_duplicate_request_id_returns_existing(client):
    first = client.post("/crm/incidents", data=VALID)
    second = client.post("/crm/incidents", data=VALID)
    assert second.headers["Location"].startswith(first.headers["Location"])
    assert "dup=1" in second.headers["Location"]

    listing = client.get("/crm/incidents").get_data(as_text=True)
    assert listing.count("REQ-001") == 1


@pytest.mark.parametrize("field, value", [
    ("customer_id", "C999"),
    ("category", "NOPE"),
    ("summary", ""),
    ("risk_level", "EXTREME"),
    ("request_id", "R" * 51),
])
def test_invalid_input_rejected(client, field, value):
    res = client.post("/crm/incidents", data={**VALID, field: value})
    assert res.status_code == 400
    assert 'id="form-error"' in res.get_data(as_text=True)


def test_unknown_incident_404(client):
    assert client.get("/crm/incidents/INC-NONE").status_code == 404


def test_concurrent_same_request_id_returns_existing(client, monkeypatch):
    first = client.post("/crm/incidents", data=VALID)

    # 다른 요청이 중복 확인을 이미 통과한 상황: 첫 SELECT만 "없음"으로 응답
    real_connect = app_module.get_db_connection

    class RacingConn:
        def __init__(self):
            self.conn, self.missed = real_connect(), False

        def execute(self, sql, *args):
            if "WHERE request_id" in sql and not self.missed:
                self.missed = True
                return self.conn.execute("SELECT 1 WHERE 0")
            return self.conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    monkeypatch.setattr(app_module, "get_db_connection", RacingConn)
    second = client.post("/crm/incidents", data=VALID)

    assert second.status_code == 302
    assert second.headers["Location"].startswith(first.headers["Location"])
    assert "dup=1" in second.headers["Location"]


def test_incident_id_collision_retries(client, monkeypatch):
    a, b = uuid.UUID("aaaaaa" + "0" * 26), uuid.UUID("bbbbbb" + "0" * 26)
    ids = iter([a, a, b])
    monkeypatch.setattr(app_module, "uuid4", lambda: next(ids))
    manual = {**VALID, "request_id": ""}

    first = client.post("/crm/incidents", data=manual)
    second = client.post("/crm/incidents", data=manual)

    assert first.status_code == second.status_code == 302
    assert first.headers["Location"] != second.headers["Location"]


def test_db_locked_returns_503_retry_page(client, monkeypatch):
    def locked():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app_module, "get_db_connection", locked)

    res = client.post("/crm/incidents", data=VALID)
    assert res.status_code == 503
    assert 'id="crm-error"' in res.get_data(as_text=True)

    api = client.get("/api/customers")
    assert api.status_code == 503 and api.json["retryable"] is True


def test_missing_table_tells_to_run_seed(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "DB_PATH", tmp_path / "empty.db")

    res = client.get("/crm/incidents")
    html = res.get_data(as_text=True)
    assert res.status_code == 500
    assert 'id="crm-setup-error"' in html and "seed.py" in html


def test_old_schema_db_is_not_retryable(client, monkeypatch, tmp_path):
    # finguard.db를 다시 만들지 않아 incidents에 request_id 컬럼이 없는 경우
    old_db = tmp_path / "old.db"
    seed.initialize_database(old_db)
    conn = sqlite3.connect(old_db)
    conn.executescript(
        "DROP TABLE incidents;"
        "CREATE TABLE incidents (incident_id TEXT PRIMARY KEY,"
        " consultation_id TEXT NOT NULL, status TEXT DEFAULT 'PENDING');"
    )
    conn.close()
    monkeypatch.setattr(app_module, "DB_PATH", old_db)

    res = client.post("/crm/incidents", data=VALID)
    html = res.get_data(as_text=True)
    assert res.status_code == 500
    assert 'id="crm-setup-error"' in html and 'id="crm-error"' not in html

    api = client.get("/api/customers")
    assert api.status_code == 200


def test_repeated_id_conflict_returns_503(client, monkeypatch):
    monkeypatch.setattr(app_module, "uuid4", lambda: uuid.UUID("c" * 32))
    manual = {**VALID, "request_id": ""}

    assert client.post("/crm/incidents", data=manual).status_code == 302
    res = client.post("/crm/incidents", data=manual)
    assert res.status_code == 503
    assert client.get("/crm/incidents").get_data(as_text=True).count(">INC-") == 1


def test_retry_after_lost_response_gets_same_number(client):
    # 저장은 됐지만 응답을 못 받은 UiPath가 같은 request_id로 다시 접수하는 경우
    first = client.post("/crm/incidents", data=VALID).headers["Location"]
    retry = client.post("/crm/incidents", data=VALID, follow_redirects=True)
    html = retry.get_data(as_text=True)
    assert first.rsplit("/", 1)[1] in html
    assert 'id="duplicate-notice"' in html


def test_consultation_page_has_required_fields(client):
    html = client.get("/consultation").get_data(as_text=True)
    for element_id in ("customer_id", "conversation", "analyze-btn",
                       "result-category", "result-summary", "result-risk", "approve-btn"):
        assert f'id="{element_id}"' in html


def test_fds_customer_risk_endpoint(client):
    res = client.get("/api/fds/customers/C001")
    assert res.status_code == 200
    assert res.json["risk_level"] in ("LOW", "MEDIUM", "HIGH")


def test_fds_customer_risk_unknown_customer_is_low(client):
    res = client.get("/api/fds/customers/NOPE")
    assert res.status_code == 200
    assert res.json == {"transaction_id": None, "risk_score": 0,
                         "risk_level": "LOW", "reasons": []}
