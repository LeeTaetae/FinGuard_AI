import os
import time

import pytest

from app import app, limiter


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
    print(f"UiPath Gateway HTTP {response.status_code} ({time.perf_counter() - start:.2f}s)")
    assert response.status_code == 200, response.json.get("error")
    assert set(response.json) == {
        "category", "recommended_action", "requires_human_review", "suggested_reply", "summary",
    }
    assert response.json["category"] == "UNAUTHORIZED_TRANSACTION"
    assert response.json["recommended_action"] == "VERIFY_AND_ESCALATE"
    assert response.json["requires_human_review"] is True
    assert response.json["summary"].strip()
    assert response.json["suggested_reply"].strip()
