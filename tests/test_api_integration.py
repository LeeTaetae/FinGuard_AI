from json import JSONDecodeError

import pytest
from unittest.mock import patch

from ai.consultation_analyzer import ConsultationResult
from app import app, limiter


MOCK_RESULT = {
    "category": "UNAUTHORIZED_TRANSACTION",
    "summary": "고객이 승인하지 않은 해외 결제를 문의함",
    "suggested_reply": "본인 확인 후 담당 상담원이 안내하겠습니다.",
    "recommended_action": "VERIFY_AND_ESCALATE",
    "requires_human_review": True,
}


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    limiter.reset()
    with app.test_client() as client:
        yield client


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json["status"] == "ok"


@patch("app.analyze_consultation", return_value=MOCK_RESULT)
def test_ai_analyze_success(mock_ai, client):
    response = client.post(
        "/api/ai/analyze",
        json={"conversation": "해외에서 결제된 적이 없어요."},
    )

    assert response.status_code == 200
    assert response.json == MOCK_RESULT
    mock_ai.assert_called_once()


def test_ai_empty_conversation(client):
    response = client.post(
        "/api/ai/analyze",
        json={"conversation": ""},
    )

    assert response.status_code == 400


def test_ai_invalid_json(client):
    response = client.post(
        "/api/ai/analyze",
        data="invalid-json",
        content_type="application/json",
    )

    assert response.status_code == 400


def test_ai_too_long(client):
    response = client.post(
        "/api/ai/analyze",
        json={"conversation": "가" * 10001},
    )

    assert response.status_code == 400


@patch("app.analyze_consultation", side_effect=RuntimeError("API Error"))
def test_ai_api_failure(mock_ai, client):
    response = client.post(
        "/api/ai/analyze",
        json={"conversation": "카드 결제 문의입니다."},
    )

    assert response.status_code == 502


@patch("app.analyze_consultation", return_value=MOCK_RESULT)
def test_ai_rate_limit(mock_ai, client):
    for _ in range(5):
        response = client.post(
            "/api/ai/analyze",
            json={"conversation": "카드 결제 문의입니다."},
        )
        assert response.status_code == 200

    response = client.post(
        "/api/ai/analyze",
        json={"conversation": "카드 결제 문의입니다."},
    )

    assert response.status_code == 429
    assert "error" in response.json


def test_other_api_not_rate_limited(client):
    for _ in range(7):
        response = client.get("/health")
        assert response.status_code == 200


@pytest.mark.parametrize("unsafe", [
    "거래를 취소했습니다.",
    "카드 정지가 완료되었습니다.",
    "고객님께 연락드리겠습니다.",
    "결제 취소를 보장합니다.",
])
def test_ai_gateway_result_passes_safety_validation(client, unsafe):
    result = ConsultationResult(**{
        **MOCK_RESULT,
        "suggested_reply": unsafe,
        "summary": unsafe,
        "requires_human_review": False,
    })
    with patch("ai.uipath_client.UiPathAzureChatOpenAI") as sdk:
        sdk.return_value.with_structured_output.return_value.invoke.return_value = result
        response = client.post("/api/ai/analyze", json={"conversation": "가상의 해외 결제 문의"})

    assert response.status_code == 200
    assert set(response.json) == set(MOCK_RESULT)
    assert response.json["suggested_reply"] != unsafe
    assert response.json["summary"] != unsafe
    assert response.json["requires_human_review"] is True


@pytest.mark.parametrize(("failure", "message"), [
    (401, "인증"),
    (403, "권한"),
    (TimeoutError("private detail"), "초과"),
    (JSONDecodeError("private detail", "{", 0), "구조화 응답"),
    (None, "구조화 응답"),
    ({"category": "missing other fields"}, "구조화 응답"),
])
def test_ai_gateway_failure_requires_review(client, failure, message):
    with patch("ai.uipath_client.UiPathAzureChatOpenAI") as sdk:
        invoke = sdk.return_value.with_structured_output.return_value.invoke
        if isinstance(failure, int):
            status = failure
            failure = RuntimeError("private detail")
            failure.status_code = status
        if isinstance(failure, Exception):
            invoke.side_effect = failure
        else:
            invoke.return_value = failure
        response = client.post("/api/ai/analyze", json={"conversation": "가상의 카드 분실 상담"})

    assert response.status_code == 502
    assert message in response.json["error"]
    assert "private detail" not in response.json["error"]
    assert response.json["recommended_action"] == "ESCALATE_TO_AGENT"
    assert response.json["requires_human_review"] is True
    assert set(response.json) == {"error", "recommended_action", "requires_human_review"}
