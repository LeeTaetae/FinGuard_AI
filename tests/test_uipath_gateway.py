import os
import secrets
import time
from uuid import uuid4

import pytest

import app as web
from app import app, limiter
from database.seed import initialize_database
from test_local_auth import login, post


CRM_CONVERSATION = (
    "제가 해외에 나간 적이 없는데 해외에서 150만 원이 결제됐다는 문자를 받았습니다. "
    "제가 결제한 내역이 아닙니다. 카드 사용을 정지하고 부정 결제 사고를 접수해 주세요."
)


@pytest.mark.gateway
@pytest.mark.skipif(
    os.getenv("RUN_UIPATH_GATEWAY_TESTS") != "1",
    reason="set RUN_UIPATH_GATEWAY_TESTS=1 to call the live UiPath Gateway",
)
def test_live_uipath_gateway(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    limiter.reset()
    start = time.perf_counter()
    with app.test_client() as client:
        response = client.post("/api/ai/analyze", json={
            "conversation": "해외에 간 적이 없는데 가상의 해외 상점에서 결제됐어요. 제가 승인한 거래가 아니에요.",
        })
    print(f"Flask /api/ai/analyze HTTP {response.status_code} ({time.perf_counter() - start:.2f}s)")
    assert response.status_code == 200, response.json.get("error")
    assert set(response.json) == {
        "category", "recommended_action", "requires_human_review", "suggested_reply", "summary",
    }
    assert response.json["category"] == "UNAUTHORIZED_TRANSACTION"
    assert response.json["recommended_action"] == "VERIFY_AND_ESCALATE"
    assert response.json["requires_human_review"] is True
    assert response.json["summary"].strip()
    assert response.json["suggested_reply"].strip()


@pytest.mark.gateway
@pytest.mark.skipif(
    os.getenv("RUN_UIPATH_GATEWAY_TESTS") != "1",
    reason="set RUN_UIPATH_GATEWAY_TESTS=1 to call the live UiPath Gateway",
)
def test_live_uipath_gateway_automation_request(tmp_path, monkeypatch):
    db = tmp_path / "gateway-crm.sqlite"
    initialize_database(db)
    monkeypatch.setattr(web, "DB_PATH", db)
    monkeypatch.setitem(app.config, "SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setitem(app.config, "SESSION_COOKIE_SECURE", False)
    limiter.reset()
    password = secrets.token_urlsafe(24)
    account = app.test_cli_runner().invoke(
        args=["create-user", "gateway-agent", "--role", "AGENT"],
        input=f"{password}\n{password}\n",
    )
    if account.exit_code != 0:
        pytest.fail("Temporary CRM account creation failed", pytrace=False)

    with app.test_client() as client:
        if login(client, username="gateway-agent", password=password)[0].status_code != 302:
            pytest.fail("Temporary CRM account login failed", pytrace=False)
        payload = {"customer_id": "C001", "conversation": CRM_CONVERSATION}
        key = uuid4().hex
        response = post(client, "/api/automation/requests", payload, key)
        if response.status_code != 201:
            pytest.fail(f"CRM automation request returned HTTP {response.status_code}", pytrace=False)
        saved = response.get_json()
        result = saved.get("ai_result")
        if not result:
            pytest.fail("CRM saved a review request without AI results. See the sanitized Flask log.",
                        pytrace=False)
        if (saved.get("status") != "PENDING_APPROVAL" or not saved.get("review_required")
                or result.get("category") != "UNAUTHORIZED_TRANSACTION"
                or result.get("recommended_action") != "VERIFY_AND_ESCALATE"
                or not result.get("summary", "").strip() or not result.get("suggested_reply", "").strip()):
            pytest.fail("CRM automation request returned an unexpected structured result", pytrace=False)
        retry = post(client, "/api/automation/requests", payload, key)
        if retry.status_code != 200 or retry.get_json() != saved:
            pytest.fail("CRM idempotency replay did not return the saved result", pytrace=False)
