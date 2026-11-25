import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

import app as web
from database.migrate import apply_migrations
from database.seed import initialize_database
from services import automation_service as automation
from services.incident_service import IncidentValidationError, create_incident


AI = {
    "category": "LOST_CARD",
    "summary": "가상의 고객이 카드 분실을 문의함",
    "suggested_reply": "카드 분실 신고 절차를 확인해 주세요.",
    "recommended_action": "VERIFY_CUSTOMER",
    "requires_human_review": True,
}
FIELDS = {"category": "LOST_CARD", "summary": "상담원이 확인한 분실 문의", "risk_level": "HIGH"}
URL = "/api/automation/requests"
PERMISSIONS = {
    "operator": {"create", "read"},
    "approver": {"create", "read", "decide"},
    "other": {"read"},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite"
    initialize_database(db)
    monkeypatch.setattr(web, "DB_PATH", db)
    monkeypatch.setattr(web, "analyze_consultation", lambda _: AI)
    monkeypatch.setitem(web.app.config, "TESTING", True)
    monkeypatch.setitem(web.app.config, "AUTOMATION_AUTHORIZER",
                        lambda actor, permission: permission in PERMISSIONS.get(actor, ()))
    web.limiter.reset()
    with web.app.test_client() as client:
        yield client


def identity(actor):
    return {"REMOTE_USER": actor} if actor else {}


def create(client, *, actor="operator", key="case-1", text="가상의 카드 분실 상담"):
    return client.post(URL, json={"customer_id": "C001", "conversation": text},
                       headers={"Idempotency-Key": key}, environ_overrides=identity(actor))


def decide(client, request_id, action="approve", actor="approver", fields=None):
    return client.post(f"{URL}/{request_id}/{action}",
                       json=FIELDS if fields is None else fields, environ_overrides=identity(actor))


def rows(sql, parameters=()):
    with closing(web.get_db_connection()) as conn:
        return conn.execute(sql, parameters).fetchall()


def test_migration_preserves_legacy_tables_and_can_be_run_twice(tmp_path):
    db = tmp_path / "legacy.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript((web.BASE_DIR / "database/schema.sql").read_text(encoding="utf-8"))
        with conn:
            conn.execute("INSERT INTO customers (customer_id, name) VALUES ('C', '가상')")
            conn.execute("INSERT INTO transactions (transaction_id, customer_id, amount, country,"
                         " transaction_at) VALUES ('T', 'C', 50, 'KR', '2026-01-01 00:00:00')")
            conn.execute("INSERT INTO consultations (consultation_id, customer_id, conversation)"
                         " VALUES ('S', 'C', '기존 상담')")
            conn.execute("INSERT INTO incidents (incident_id, request_id, customer_id, category,"
                         " summary, consultation_id, transaction_id)"
                         " VALUES ('I', 'R', 'C', 'OTHER', '기존 접수', 'S', 'T')")
        tables = ("customers", "transactions", "consultations", "incidents")
        before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
        apply_migrations(conn)
        apply_migrations(conn)
        assert before == {
            table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables
        }
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        request_types = {row[1]: row[2] for row in
                         conn.execute("PRAGMA table_info(automation_requests)")}
        for fk in conn.execute("PRAGMA foreign_key_list(automation_requests)"):
            _, _, table, column, parent_key, *_ = fk
            parent = {row[1]: row[2] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert request_types[column] == parent[parent_key] == "TEXT"


def test_legacy_incident_layout_preserves_rows_and_enables_crm(client):
    with closing(web.get_db_connection()) as conn:
        conn.executescript("""
            DROP TABLE automation_events;
            DROP TABLE automation_requests;
            DROP TABLE incidents;
            CREATE TABLE incidents (
                incident_id TEXT PRIMARY KEY,
                consultation_id TEXT NOT NULL REFERENCES consultations(consultation_id),
                transaction_id TEXT REFERENCES transactions(transaction_id),
                risk_score INTEGER,
                risk_level TEXT,
                status TEXT DEFAULT 'PENDING',
                rpa_status TEXT DEFAULT 'NOT_STARTED',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO consultations (consultation_id, customer_id, category, ai_summary)
            VALUES ('LEGACY-CONS', 'C001', 'LOST_CARD', '가상의 기존 상담');
            INSERT INTO incidents
                (incident_id, consultation_id, risk_score, risk_level, status, rpa_status)
            VALUES ('LEGACY-INC', 'LEGACY-CONS', 60, 'HIGH', 'PENDING', 'COMPLETED');
        """)
        old = conn.execute("SELECT * FROM incidents").fetchone()
        apply_migrations(conn)
        apply_migrations(conn)
        updated = conn.execute("SELECT * FROM incidents").fetchone()
        assert {key: updated[key] for key in old.keys()} == dict(old)
        assert (updated["customer_id"], updated["category"], updated["summary"]) == (
            "C001", "LOST_CARD", "가상의 기존 상담",
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    assert client.get("/crm/incidents/LEGACY-INC").status_code == 200
    manual = client.post("/crm/incidents", data={**FIELDS, "customer_id": "C001"})
    assert manual.status_code == 302
    assert create(client).status_code == 201


def test_create_persists_analysis_and_is_idempotent(client, monkeypatch):
    first = create(client)
    assert first.status_code == 201
    state = first.json
    assert state["status"] == "PENDING_APPROVAL"
    assert state["incident_id"] is None
    assert state["dispatch_pending"] is False
    assert state["ai_result"] == AI
    assert state["transaction_id"] == state["fds_result"]["transaction_id"]
    monkeypatch.setattr(web, "analyze_consultation", lambda _: pytest.fail("replayed AI"))
    retry = create(client)
    assert retry.status_code == 200 and retry.json == state
    assert create(client, text="다른 상담").status_code == 409
    assert rows("SELECT conversation FROM consultations WHERE consultation_id = ?",
                (state["consultation_id"],))[0][0] == "가상의 카드 분실 상담"
    assert len(rows("SELECT * FROM automation_requests")) == 1
    assert len(rows("SELECT * FROM automation_events")) == 1


def test_concurrent_creation_persists_one_request(client, monkeypatch):
    barrier = Barrier(2)

    def analyze(_):
        barrier.wait(timeout=10)
        return AI

    monkeypatch.setattr(web, "analyze_consultation", analyze)

    def worker(_):
        with web.app.test_client() as worker_client:
            response = create(worker_client)
            return response.status_code, response.json["request_id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    assert sorted(status for status, _ in results) == [200, 201]
    assert len({request_id for _, request_id in results}) == 1
    assert len(rows("SELECT * FROM automation_requests")) == 1
    assert len(rows("SELECT * FROM automation_events")) == 1


def test_permissions_fail_closed_and_ignore_spoofed_actor(client, monkeypatch):
    assert client.post(URL, json={"approved_by": "approver"},
                       headers={"Remote-User": "approver", "X-User": "approver"}).status_code == 401
    state = create(client).json
    request_id = state["request_id"]
    assert decide(client, request_id, actor=None).status_code == 401
    assert decide(client, request_id, actor="operator",
                  fields={**FIELDS, "approved_by": "approver"}).status_code == 403
    for suffix in ("", "/events"):
        assert client.get(f"{URL}/{request_id}{suffix}",
                          environ_overrides=identity("other")).status_code == 404
    monkeypatch.setitem(web.app.config, "AUTOMATION_AUTHORIZER", None)
    assert decide(client, request_id).status_code == 403
    assert rows("SELECT status FROM automation_requests")[0][0] == "PENDING_APPROVAL"


def test_approval_logs_actor_and_dispatch_intent_without_creating_incident(client):
    request_id = create(client).json["request_id"]
    for status in ("PENDING_APPROVAL", "APPROVED"):
        if status == "APPROVED":
            approval = decide(client, request_id, fields={**FIELDS, "approved_by": "forged"})
            assert approval.status_code == 200
            assert approval.json["decision_by"] == "approver"
            assert approval.json["decision_at"]
            assert approval.json["dispatch_pending"] is True
            assert approval.json["status"] == "APPROVED"
            assert decide(client, request_id).json == approval.json
            assert decide(client, request_id, fields={**FIELDS, "summary": "변경"}).status_code == 409
            assert decide(client, request_id, action="reject").status_code == 409

        direct = client.post("/crm/incidents", data={**FIELDS, "request_id": request_id,
                                                     "customer_id": "C001"})
        assert direct.status_code == 400
    events = client.get(f"{URL}/{request_id}/events", environ_overrides=identity("operator"))
    assert events.headers["Cache-Control"] == "no-store"
    assert [(event["from_status"], event["to_status"], event["actor_id"])
            for event in events.json] == [
        (None, "PENDING_APPROVAL", "operator"),
        ("PENDING_APPROVAL", "APPROVED", "approver"),
    ]
    assert rows("SELECT * FROM incidents") == []


def test_rejection_is_terminal_and_retry_safe(client):
    request_id = create(client).json["request_id"]
    rejection = decide(client, request_id, action="reject")
    assert rejection.status_code == 200
    assert rejection.json["status"] == "REJECTED"
    assert rejection.json["dispatch_pending"] is False
    assert decide(client, request_id, action="reject").json == rejection.json
    assert decide(client, request_id).status_code == 409
    assert len(rows("SELECT * FROM automation_events")) == 2


def test_simultaneous_approve_and_reject_have_one_winner(client):
    request_id = create(client).json["request_id"]
    barrier = Barrier(2)

    def worker(action):
        with web.app.test_client() as worker_client:
            barrier.wait(timeout=10)
            return decide(worker_client, request_id, action=action).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(worker, ["approve", "reject"])) == [200, 409]
    assert len(rows("SELECT * FROM automation_events")) == 2


def test_failed_ai_is_saved_for_manual_review(client, monkeypatch):
    def unavailable(_):
        raise web.UiPathLLMError("private gateway detail")

    monkeypatch.setattr(web, "analyze_consultation", unavailable)
    response = create(client)
    assert response.status_code == 201
    assert response.json["review_required"] is True
    assert response.json["ai_result"] is None
    assert response.json["status"] == "PENDING_APPROVAL"
    assert "private gateway detail" not in response.get_data(as_text=True)
    assert decide(client, response.json["request_id"], fields={}).status_code == 400
    assert decide(client, response.json["request_id"]).status_code == 200


def test_creation_rolls_back_all_records_if_event_write_fails(client):
    before = rows("SELECT COUNT(*) FROM consultations")[0][0]
    with closing(web.get_db_connection()) as conn, conn:
        conn.execute("""
            CREATE TRIGGER break_create BEFORE INSERT ON automation_events
            BEGIN SELECT RAISE(ABORT, 'injected creation failure'); END
        """)
    assert create(client).status_code == 503
    assert rows("SELECT COUNT(*) FROM consultations")[0][0] == before
    assert rows("SELECT * FROM automation_requests") == []
    with closing(web.get_db_connection()) as conn, conn:
        conn.execute("DROP TRIGGER break_create")
    assert create(client).status_code == 201


def test_event_failure_rolls_back_approval_and_retry_recovers(client):
    request_id = create(client).json["request_id"]
    with closing(web.get_db_connection()) as conn, conn:
        conn.execute("""
            CREATE TRIGGER break_event BEFORE INSERT ON automation_events
            BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END
        """)
    assert decide(client, request_id).status_code == 503
    state = client.get(f"{URL}/{request_id}", environ_overrides=identity("operator")).json
    assert state["status"] == "PENDING_APPROVAL"
    assert state["decision_by"] is None and state["dispatch_pending"] is False
    assert len(rows("SELECT * FROM automation_events")) == 1
    with closing(web.get_db_connection()) as conn, conn:
        conn.execute("DROP TRIGGER break_event")
    assert decide(client, request_id).status_code == 200


def test_execution_transitions_require_execution_id_and_matching_incident(client):
    request_id = create(client).json["request_id"]
    with closing(web.get_db_connection()) as conn:
        def record(target, execution_id="job-simulated", incident_id=None):
            return automation.record_execution(
                conn, request_id, target, actor="test-worker",
                execution_id=execution_id, incident_id=incident_id,
            )

        with pytest.raises(automation.AutomationConflict):
            record(automation.Status.QUEUED)
        assert decide(client, request_id).status_code == 200
        with pytest.raises(automation.AutomationConflict):
            record(automation.Status.QUEUED, execution_id="")
        with pytest.raises(automation.AutomationConflict):
            record(automation.Status.RUNNING)
        queued = record(automation.Status.QUEUED)
        assert queued["status"] == "QUEUED" and queued["dispatch_pending"] is False
        assert record(automation.Status.QUEUED) == queued
        with pytest.raises(automation.AutomationConflict):
            record(automation.Status.RUNNING, execution_id="different-job")
        assert record(automation.Status.RUNNING)["status"] == "RUNNING"
        with pytest.raises(automation.AutomationConflict):
            record(automation.Status.SUCCEEDED, incident_id="absent")
        with conn:
            conn.execute("""
                INSERT INTO incidents
                    (incident_id, request_id, customer_id, category, summary, consultation_id)
                VALUES ('I-TEST', ?, 'C001', 'LOST_CARD', '가상 접수', ?)
            """, (request_id, queued["consultation_id"]))
        assert record(automation.Status.SUCCEEDED, incident_id="I-TEST")["incident_id"] == "I-TEST"
        with pytest.raises(automation.AutomationConflict):
            record(automation.Status.FAILED)
    retry = client.post("/crm/incidents", data={**FIELDS, "request_id": request_id,
                                                "customer_id": "C001"})
    assert retry.status_code == 302 and "/I-TEST?dup=1" in retry.location
    assert len(rows("SELECT * FROM incidents")) == 1


def test_failed_execution_is_terminal_and_never_creates_incident(client):
    request_id = create(client).json["request_id"]
    assert decide(client, request_id).status_code == 200
    execution = {"actor": "test-worker", "execution_id": "failed-job"}
    with closing(web.get_db_connection()) as conn:
        automation.record_execution(conn, request_id, automation.Status.QUEUED, **execution)
        failed = automation.record_execution(conn, request_id, automation.Status.FAILED, **execution)
        assert failed["status"] == "FAILED" and not failed["dispatch_pending"]
        assert automation.record_execution(
            conn, request_id, automation.Status.FAILED, **execution
        ) == failed
        with pytest.raises(automation.AutomationConflict):
            automation.record_execution(conn, request_id, automation.Status.RUNNING, **execution)
    assert rows("SELECT * FROM incidents") == []


def test_shared_incident_service_and_html_route_use_same_idempotency(client):
    fields = {**FIELDS, "request_id": "LEGACY-SHARED", "customer_id": "C001"}
    with closing(web.get_db_connection()) as conn:
        incident_id, duplicate = create_incident(conn, fields, categories=web.CATEGORY_LABELS)
        assert incident_id.startswith("INC-") and not duplicate
        with pytest.raises(IncidentValidationError):
            create_incident(conn, {**fields, "summary": ""}, categories=web.CATEGORY_LABELS)
    response = client.post("/crm/incidents", data=fields)
    assert response.status_code == 302
    assert f"/{incident_id}?dup=1" in response.location


def test_consultation_ui_links_to_approval_only_with_permissions(client):
    legacy = client.get("/consultation").get_data(as_text=True)
    assert 'action="/crm/incidents"' in legacy and 'id="reject-btn"' not in legacy
    operator = client.get("/consultation", environ_overrides=identity("operator")).get_data(as_text=True)
    assert 'const automationEnabled = true' in operator and 'id="reject-btn"' not in operator
    approver = client.get("/consultation", environ_overrides=identity("approver")).get_data(as_text=True)
    assert 'id="reject-btn"' in approver and 'const canDecide = true' in approver
