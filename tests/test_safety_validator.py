
import pytest

from ai.safety_validator import (
    detect_unsafe_reply,
    validate_safety,
)


# ==========================================
# 1. 테스트용 AI 응답 생성
# ==========================================

def make_result(
    category="GENERAL_INQUIRY",
    reply="고객님, 문의하신 내용을 안내해 드리겠습니다.",
    requires_human_review=False,
):
    return {
        "category": category,
        "summary": "고객이 상담을 요청했습니다.",
        "suggested_reply": reply,
        "recommended_action": "PROVIDE_INFORMATION",
        "requires_human_review": requires_human_review,
    }


# ==========================================
# 2. 위험 표현 탐지 테스트
# ==========================================

@pytest.mark.parametrize(
    "reply",
    [
        "거래를 확인했습니다.",
        "거래 내역을 조회했습니다.",
        "이상 거래가 확인되었습니다.",
        "사고 접수를 진행하겠습니다.",
        "사고 접수가 완료되었습니다.",
        "카드 정지했습니다.",
        "환불 완료했습니다.",
        "재발급 완료했습니다.",
        "승인하지 않은 거래로 보입니다.",
        "미승인 거래로 확인되었습니다.",
        "사기 거래로 확인되었습니다.",
    ],
)
def test_detect_unsafe_reply(reply):
    assert detect_unsafe_reply(reply) is True


# ==========================================
# 3. 정상 답변 탐지 테스트
# ==========================================

@pytest.mark.parametrize(
    "reply",
    [
        "고객님, 본인 확인 후 절차를 안내해 드리겠습니다.",
        "거래 내역은 담당 상담원의 확인이 필요합니다.",
        "카드 분실 신고 방법을 안내해 드리겠습니다.",
        "고객님께서 승인하지 않았다고 말씀하신 거래입니다.",
        "정확한 결제 금액은 공식 앱에서 확인하실 수 있습니다.",
    ],
)
def test_detect_safe_reply(reply):
    assert detect_unsafe_reply(reply) is False


# ==========================================
# 4. 빈 문자열 및 잘못된 입력 테스트
# ==========================================

@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   ",
        None,
        123,
    ],
)
def test_invalid_reply(reply):
    assert detect_unsafe_reply(reply) is True


# ==========================================
# 5. 카드 분실 상담원 검토 강제
# ==========================================

def test_lost_card_requires_human_review():
    result = make_result(
        category="LOST_CARD",
        reply="고객님, 카드 분실 신고 절차를 안내하겠습니다.",
    )

    validated = validate_safety(result)

    assert validated["category"] == "LOST_CARD"
    assert validated["requires_human_review"] is True


# ==========================================
# 6. 미승인 거래 상담원 검토 강제
# ==========================================

def test_unauthorized_transaction_requires_review():
    result = make_result(
        category="UNAUTHORIZED_TRANSACTION",
        reply="거래 내역에 대한 확인이 필요합니다.",
    )

    validated = validate_safety(result)

    assert validated["category"] == "UNAUTHORIZED_TRANSACTION"
    assert validated["requires_human_review"] is True


# ==========================================
# 7. 위험 답변 자동 교체
# ==========================================

def test_unsafe_reply_is_replaced():
    result = make_result(
        category="UNAUTHORIZED_TRANSACTION",
        reply="승인하지 않은 거래로 보입니다.",
    )

    validated = validate_safety(result)

    assert validated["requires_human_review"] is True

    assert (
        validated["suggested_reply"]
        != "승인하지 않은 거래로 보입니다."
    )

    assert (
        "담당 상담원"
        in validated["suggested_reply"]
    )


# ==========================================
# 8. 일반 문의 위험 답변 교체
# ==========================================

def test_general_inquiry_unsafe_reply():
    result = make_result(
        category="GENERAL_INQUIRY",
        reply="거래를 확인했습니다.",
    )

    validated = validate_safety(result)

    assert validated["requires_human_review"] is True

    assert (
        "담당 상담원"
        in validated["suggested_reply"]
    )


# ==========================================
# 9. 정상 답변 유지
# ==========================================

def test_safe_reply_is_preserved():
    original_reply = (
        "고객님, 정확한 내용은 "
        "담당 상담원을 통해 확인하실 수 있습니다."
    )

    result = make_result(
        category="GENERAL_INQUIRY",
        reply=original_reply,
    )

    validated = validate_safety(result)

    assert validated["suggested_reply"] == original_reply
    assert validated["requires_human_review"] is False


# ==========================================
# 10. 기존 JSON 구조 유지
# ==========================================

def test_json_schema_is_preserved():
    result = make_result(
        category="LOST_CARD",
        reply="사고 접수가 완료되었습니다.",
    )

    validated = validate_safety(result)

    expected_keys = {
        "category",
        "summary",
        "suggested_reply",
        "recommended_action",
        "requires_human_review",
    }

    assert set(validated.keys()) == expected_keys

    assert isinstance(validated["category"], str)
    assert isinstance(validated["summary"], str)
    assert isinstance(validated["suggested_reply"], str)
    assert isinstance(validated["recommended_action"], str)
    assert isinstance(validated["requires_human_review"], bool)


# ==========================================
# 11. 원본 데이터 변경 방지
# ==========================================

def test_original_result_is_not_modified():
    result = make_result(
        category="LOST_CARD",
        reply="카드 정지했습니다.",
    )

    original = result.copy()

    validated = validate_safety(result)

    assert result == original
    assert validated is not result
    assert validated["requires_human_review"] is True


# ==========================================
# 12. 잘못된 분석 결과 입력
# ==========================================

@pytest.mark.parametrize(
    "result",
    [
        None,
        "",
        123,
        [],
    ],
)
def test_invalid_result_type(result):
    with pytest.raises(ValueError):
        validate_safety(result)
