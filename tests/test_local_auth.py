import re
import secrets
from contextlib import closing

import pytest

import app as web
from database.seed import initialize_database


PASSWORD = "fixture-password-" + secrets.token_hex(8)
AI = {
    "category": "UNAUTHORIZED_TRANSACTION",
    "summary": "가상 해외 결제 문의",
    "suggested_reply": "거래 내역 확인을 안내합니다.",
    "recommended_action": "VERIFY_AND_ESCALATE",
    "requires_human_review": True,
}
URL = "/api/automation/requests"
FIELDS = {"category": "UNAUTHORIZED_TRANSACTION", "summary": "검토한 가상 상담", "risk_level": "HIGH"}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    db = tmp_path / "auth.db"
    initialize_database(db)
    monkeypatch.setattr(web, "DB_PATH", db)
    monkeypatch.setattr(web, "analyze_consultation", lambda _: AI)
    monkeypatch.setitem(web.app.config, "SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setitem(web.app.config, "TESTING", True)
    monkeypatch.setitem(web.app.config, "SESSION_COOKIE_SECURE", False)
    web.limiter.reset()
    runner = web.app.test_cli_runner()
    for role in ("AGENT", "SUPERVISOR", "ADMIN"):
        result = runner.invoke(args=["create-user", role.lower(), "--role", role],
                               input=f"{PASSWORD}\n{PASSWORD}\n")
        assert result.exit_code == 0, result.output
    return web.app.test_client()


def login(client, username="agent", password=PASSWORD):
    page = client.get("/login")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
    response = client.post("/login", data={
        "username": username, "password": password, "csrf_token": token,
    })
    return response, token


def csrf(client):
    with client.session_transaction() as session:
        return session["csrf"]


def post(client, path, data, key=None):
    headers = {"X-CSRF-Token": csrf(client)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(path, json=data, headers=headers)


def create(client, key):
    return post(client, URL, {"customer_id": "C001", "conversation": "가상 고객의 해외 결제 문의"}, key)


def test_login_csrf_password_hash_and_session_rotation(demo):
    assert demo.get("/consultation").location.endswith("/login")
    assert demo.get("/api/customers").status_code == 401
    assert demo.post("/login", data={"username": "agent", "password": PASSWORD}).status_code == 403
    assert login(demo, password="wrong-password")[0].status_code == 401
    signed_in, anonymous_token = login(demo)
    assert signed_in.status_code == 302
    assert anonymous_token != csrf(demo)
    cookie = signed_in.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Expires=" in cookie and PASSWORD not in cookie
    with closing(web.get_db_connection()) as conn:
        hashed = conn.execute("SELECT password_hash FROM auth_users WHERE username = 'agent'").fetchone()[0]
        assert hashed != PASSWORD and hashed.startswith("scrypt:")
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 1


def test_logout_revokes_replayed_cookie(demo):
    login(demo)
    cookie = demo.get_cookie("finguard_session").value
    assert demo.post("/logout").status_code == 403
    assert demo.post("/logout", data={"csrf_token": csrf(demo)}).status_code == 302
    assert demo.get("/api/customers").status_code == 401
    demo.set_cookie("finguard_session", cookie)
    assert demo.get("/api/customers").status_code == 401


def test_local_identity_ignores_external_headers_and_roles_are_live(demo):
    assert demo.get("/api/customers", environ_overrides={"REMOTE_USER": "admin"}).status_code == 401
    login(demo)
    assert demo.get("/auth/users").status_code == 403
    with closing(web.get_db_connection()) as conn, conn:
        conn.execute("UPDATE auth_users SET role = 'ADMIN' WHERE username = 'agent'")
    assert demo.get("/auth/users").status_code == 200
    with closing(web.get_db_connection()) as conn, conn:
        conn.execute("UPDATE auth_users SET active = 0 WHERE username = 'agent'")
    assert demo.get("/api/customers").status_code == 401


def test_robot_cannot_be_provisioned_as_human_and_repeated_user_fails(demo):
    runner = web.app.test_cli_runner()
    robot = runner.invoke(args=["create-user", "robot", "--role", "ROBOT"])
    assert robot.exit_code != 0
    duplicate = runner.invoke(args=["create-user", "agent", "--role", "ADMIN"],
                              input=f"{PASSWORD}\n{PASSWORD}\n")
    assert duplicate.exit_code != 0
    with closing(web.get_db_connection()) as conn:
        assert conn.execute("SELECT role FROM auth_users WHERE username='agent'").fetchone()[0] == "AGENT"


def test_csrf_is_required_on_automation_and_legacy_writes(demo):
    _, stale = login(demo)
    payload = {"customer_id": "C001", "conversation": "가상 상담"}
    for token in ("", stale):
        response = demo.post(URL, json=payload,
                             headers={"Idempotency-Key": "csrf", "X-CSRF-Token": token})
        assert response.status_code == 403
    assert demo.post("/api/ai/analyze", json={"conversation": "가상 상담"}).status_code == 403
    page = demo.get("/crm/incidents/new")
    assert 'id="submit-incident"' in page.text and 'name="csrf_token"' in page.text
    assert demo.post("/crm/incidents", data=FIELDS).status_code == 403


def test_operator_creates_and_supervisor_approves_from_review_screen(demo):
    login(demo)
    assert any(row["customer_id"] == "C001" for row in demo.get("/api/customers").json)
    page = demo.get("/consultation")
    assert 'id="approve-btn" type="submit" hidden' in page.text
    assert 'id="reject-btn"' not in page.text
    created = create(demo, "auth-approval")
    assert created.status_code == 201
    state = created.json
    request_id = state["request_id"]
    assert state["ai_result"] == AI and state["fds_result"]["risk_level"] in ("LOW", "MEDIUM", "HIGH")
    assert post(demo, f"{URL}/{request_id}/approve", FIELDS).status_code == 403
    assert demo.get("/consultation/review").status_code == 403
    supervisor = web.app.test_client()
    login(supervisor, "supervisor")
    assert demo.get(f"{URL}/{request_id}").json["status"] == "PENDING_APPROVAL"
    assert request_id in supervisor.get("/consultation/review").text
    review = supervisor.get(f"/consultation?request_id={request_id}")
    assert "가상 고객의 해외 결제 문의" in review.text and 'id="reject-btn"' in review.text
    assert supervisor.post(f"{URL}/{request_id}/approve", json=FIELDS).status_code == 403
    approved = post(supervisor, f"{URL}/{request_id}/approve", FIELDS).json
    assert approved["status"] == "APPROVED"
    assert approved["decision_by"] == "supervisor" and approved["decision_at"]
    assert approved["dispatch_pending"] is True and approved["incident_id"] is None
    reopened = supervisor.get(f"/consultation?request_id={request_id}").text
    assert '"decision_by": "supervisor"' in reopened and '"status": "APPROVED"' in reopened
    assert request_id not in supervisor.get("/consultation/review").text
    direct = demo.post("/crm/incidents", data={
        **FIELDS, "csrf_token": csrf(demo), "request_id": request_id, "customer_id": "C001",
    })
    assert direct.status_code == 400
    with closing(web.get_db_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0
        assert conn.execute("SELECT status, dispatch_pending FROM automation_requests").fetchone()[:] == (
            "APPROVED", 1,
        )


def test_admin_can_reject_saved_manual_review(demo, monkeypatch):
    def fail(_):
        raise web.UiPathLLMError("mock timeout")

    monkeypatch.setattr(web, "analyze_consultation", fail)
    login(demo, "admin")
    assert demo.get("/auth/users").status_code == 200
    state = create(demo, "manual-review").json
    assert state["review_required"] is True and state["ai_result"] is None
    request_id = state["request_id"]
    assert "AI 분석에 실패" in demo.get(f"/consultation?request_id={request_id}").text
    recorded = post(demo, f"{URL}/{request_id}/reject", {}).json
    assert recorded["status"] == "REJECTED" and recorded["dispatch_pending"] is False
    assert recorded["decision_by"] == "admin"
    assert [event["to_status"] for event in demo.get(f"{URL}/{request_id}/events").json] == [
        "PENDING_APPROVAL", "REJECTED",
    ]
