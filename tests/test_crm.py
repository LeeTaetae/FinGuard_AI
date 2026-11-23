import uuid

import pytest

import app as app_module
from database import seed


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(seed, "DB_PATH", db)
    monkeypatch.setattr(app_module, "DB_PATH", db)
    seed.initialize_database()
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
