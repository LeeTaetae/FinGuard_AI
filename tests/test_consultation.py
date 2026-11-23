
from unittest.mock import patch, MagicMock

import pytest

from ai.consultation_analyzer import (
    analyze_consultation,
    ConsultationResult,
    Category,
    Action,
)


@pytest.mark.parametrize(
    "category, action",
    [
        (Category.LOST_CARD, Action.VERIFY_CUSTOMER),
        (
            Category.UNAUTHORIZED_TRANSACTION,
            Action.VERIFY_AND_ESCALATE,
        ),
        (Category.PAYMENT_INQUIRY, Action.PROVIDE_INFORMATION),
        (Category.GENERAL_INQUIRY, Action.PROVIDE_INFORMATION),
        (Category.OTHER, Action.ESCALATE_TO_AGENT),
    ],
)
def test_consultation_json_schema(category, action):
    mock_result = ConsultationResult(
        category=category,
        summary="고객 상담 내용 요약",
        suggested_reply="고객님, 문의 내용을 안내드리겠습니다.",
        recommended_action=action,
        requires_human_review=False,
    )

    mock_response = MagicMock()
    mock_response.choices[0].message.parsed = mock_result

    with patch(
        "ai.consultation_analyzer.OpenAI"
    ) as mock_openai:
        mock_openai.return_value.chat.completions.parse.return_value = (
            mock_response
        )

        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "test-key"},
        ):
            result = analyze_consultation("상담 테스트")

    assert set(result.keys()) == {
        "category",
        "summary",
        "suggested_reply",
        "recommended_action",
        "requires_human_review",
    }

    assert result["category"] == category.value
    assert result["recommended_action"] == action.value

    if category in (
        Category.LOST_CARD,
        Category.UNAUTHORIZED_TRANSACTION,
    ):
        assert result["requires_human_review"] is True


def test_empty_conversation():
    with pytest.raises(ValueError):
        analyze_consultation("")


def test_invalid_conversation_type():
    with pytest.raises(ValueError):
        analyze_consultation(None)


def test_too_long_conversation():
    with pytest.raises(ValueError):
        analyze_consultation("가" * 10001)
