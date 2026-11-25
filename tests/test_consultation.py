
from unittest.mock import patch

import pytest

from ai.consultation_analyzer import (
    analyze_consultation,
    ConsultationResult,
    Category,
    Action,
    SYSTEM_PROMPT,
)


# ==========================================
# 1. 상담 유형별 JSON 스키마 검증
# ==========================================

@pytest.mark.parametrize(
    "category, action",
    [
        (
            Category.LOST_CARD,
            Action.VERIFY_CUSTOMER,
        ),
        (
            Category.UNAUTHORIZED_TRANSACTION,
            Action.VERIFY_AND_ESCALATE,
        ),
        (
            Category.PAYMENT_INQUIRY,
            Action.PROVIDE_INFORMATION,
        ),
        (
            Category.GENERAL_INQUIRY,
            Action.PROVIDE_INFORMATION,
        ),
        (
            Category.OTHER,
            Action.ESCALATE_TO_AGENT,
        ),
    ],
)
def test_consultation_json_schema(category, action):

    # 가상의 LLM 응답 생성
    mock_result = ConsultationResult(
        category=category,
        summary="고객 상담 내용 요약",
        suggested_reply="고객님, 문의 내용을 안내드리겠습니다.",
        recommended_action=action,
        requires_human_review=False,
    )

    # UiPath SDK 호출 함수 Mock 처리
    with patch(
        "ai.consultation_analyzer.generate_response",
        return_value=mock_result,
    ) as mock_generate:

        result = analyze_consultation("상담 테스트")

        # SDK 호출 함수 및 전달 인자 검증
        mock_generate.assert_called_once_with(
            system_prompt=SYSTEM_PROMPT,
            user_prompt="상담 테스트",
            response_schema=ConsultationResult,
        )

    # JSON 응답 필드 검증
    assert set(result.keys()) == {
        "category",
        "summary",
        "suggested_reply",
        "recommended_action",
        "requires_human_review",
    }

    # 상담 유형 검증
    assert result["category"] == category.value

    # 후속 조치 검증
    assert result["recommended_action"] == action.value

    # 상담 요약 및 추천 답변 검증
    assert isinstance(result["summary"], str)
    assert isinstance(result["suggested_reply"], str)
    assert isinstance(result["requires_human_review"], bool)

    # 사고 관련 상담은 상담원 검토 필수
    if category in (
        Category.LOST_CARD,
        Category.UNAUTHORIZED_TRANSACTION,
    ):
        assert result["requires_human_review"] is True


# ==========================================
# 2. 빈 상담 내용 검증
# ==========================================

def test_empty_conversation():

    with pytest.raises(ValueError):
        analyze_consultation("")


# ==========================================
# 3. 잘못된 입력 타입 검증
# ==========================================

def test_invalid_conversation_type():

    with pytest.raises(ValueError):
        analyze_consultation(None)


# ==========================================
# 4. 최대 입력 길이 검증
# ==========================================

def test_too_long_conversation():

    with pytest.raises(ValueError):
        analyze_consultation("가" * 10001)


# ==========================================
# 5. 공백 상담 내용 검증
# ==========================================

def test_whitespace_conversation():

    with pytest.raises(ValueError):
        analyze_consultation("   ")


# ==========================================
# 6. UiPath LLM 호출 실패 검증
# ==========================================

def test_uipath_call_failure():

    with patch(
        "ai.consultation_analyzer.generate_response",
        side_effect=RuntimeError("UiPath LLM 호출 실패"),
    ):

        with pytest.raises(
            RuntimeError,
            match="UiPath LLM 호출 실패",
        ):
            analyze_consultation("카드를 잃어버렸어요.")


# ==========================================
# 7. 빈 AI 분석 결과 검증
# ==========================================

def test_empty_ai_result():

    with patch(
        "ai.consultation_analyzer.generate_response",
        return_value=None,
    ):

        with pytest.raises(
            RuntimeError,
            match="AI 상담 분석 결과를 받지 못했습니다.",
        ):
            analyze_consultation("카드를 잃어버렸어요.")
