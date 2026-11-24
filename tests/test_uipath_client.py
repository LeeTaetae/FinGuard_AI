from unittest.mock import patch

import pytest
from pydantic import ValidationError

from ai.consultation_analyzer import ConsultationResult, SYSTEM_PROMPT
from ai.uipath_client import DEFAULT_MODEL, UiPathLLMError, generate_response


CONVERSATION = "가상의 고객이 카드를 잃어버렸습니다."
FIELDS = {
    "category": "LOST_CARD",
    "recommended_action": "VERIFY_CUSTOMER",
    "requires_human_review": False,
    "suggested_reply": "본인 확인 절차를 안내해 드리겠습니다.",
    "summary": "고객이 카드 분실을 문의함",
}


@pytest.mark.parametrize("model", [None, "configured-model"])
def test_structured_gateway_call(monkeypatch, model):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if model is None:
        monkeypatch.delenv("UIPATH_LLM_MODEL", raising=False)
    else:
        monkeypatch.setenv("UIPATH_LLM_MODEL", model)

    expected = ConsultationResult(**FIELDS)
    with patch("ai.uipath_client.UiPathAzureChatOpenAI") as sdk:
        structured = sdk.return_value.with_structured_output
        structured.return_value.invoke.return_value = expected

        assert generate_response(SYSTEM_PROMPT, CONVERSATION, ConsultationResult) == expected
        sdk.assert_called_once_with(
            model=model or DEFAULT_MODEL,
            temperature=0,
            max_tokens=1000,
            timeout=60,
            max_retries=2,
        )
        structured.assert_called_once_with(ConsultationResult)
        structured.return_value.invoke.assert_called_once_with([
            ("system", SYSTEM_PROMPT),
            ("human", CONVERSATION),
        ])


def test_gateway_auth_configuration_validation():
    with pytest.raises(ValidationError) as error:
        ConsultationResult.model_validate({})
    with patch("ai.uipath_client.UiPathAzureChatOpenAI", side_effect=error.value):
        with pytest.raises(UiPathLLMError, match="인증 또는 연결 설정"):
            generate_response(SYSTEM_PROMPT, CONVERSATION, ConsultationResult)
