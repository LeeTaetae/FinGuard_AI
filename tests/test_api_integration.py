
import pytest
from unittest.mock import patch

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
