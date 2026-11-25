"""Structured consultation responses through the UiPath LLM Gateway."""

import os
from json import JSONDecodeError
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from httpx import RequestError, TimeoutException
from langchain_core.exceptions import OutputParserException
from openai import APITimeoutError, LengthFinishReasonError
from pydantic import BaseModel, ValidationError
from uipath_langchain.chat.models import UiPathAzureChatOpenAI

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "gpt-4.1-mini-2025-04-14"


class UiPathLLMError(RuntimeError):
    """Sanitized error suitable for logging and showing to an operator."""

    def __init__(self, message, *, stage="unknown", reason="unknown", status=None):
        super().__init__(message)
        self.stage = stage
        self.reason = reason
        self.status = status

    @property
    def diagnostic(self):
        return f"stage={self.stage} reason={self.reason} gateway_http={self.status or '-'}"


def generate_response(
    system_prompt: str,
    user_prompt: str,
    response_schema: type[T],
) -> T:
    try:
        llm = UiPathAzureChatOpenAI(
            model=os.getenv("UIPATH_LLM_MODEL", DEFAULT_MODEL),
            temperature=0,
            max_tokens=1000,
            timeout=60,
            max_retries=2,
        )
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        reason = "configuration_invalid"
        if any("Access token is expired." in error["msg"] for error in errors):
            reason = "token_expired"
        elif any(error["type"] == "missing" for error in errors):
            reason = "configuration_missing"
        raise UiPathLLMError("UiPath 인증 또는 연결 설정을 확인해 주세요.",
                            stage="configure", reason=reason) from None
    except Exception as exc:
        raise _gateway_error(exc, stage="configure") from None

    try:
        structured = llm.with_structured_output(response_schema)
    except Exception as exc:
        raise _gateway_error(exc, stage="schema_setup") from None

    try:
        response = structured.invoke([
            ("system", system_prompt),
            ("human", user_prompt),
        ])
    except Exception as exc:
        raise _gateway_error(exc, stage="invoke") from None

    if not isinstance(response, response_schema):
        raise UiPathLLMError("UiPath의 구조화 응답을 검증할 수 없습니다.",
                            stage="invoke", reason="unexpected_response_type")

    return response


def _gateway_error(exc: Exception, *, stage: str) -> UiPathLLMError:
    status = None
    errors = []
    while exc is not None and exc not in errors:
        errors.append(exc)
        candidate = getattr(exc, "status_code", None)
        if candidate is None:
            candidate = getattr(getattr(exc, "response", None), "status_code", None)
        if type(candidate) is int and 100 <= candidate <= 599:
            status = status or candidate
        exc = exc.__cause__

    messages = {
        400: "gateway_bad_request", 401: "authentication", 403: "permission",
        404: "model_or_route_not_found", 429: "rate_limit_or_quota",
    }
    message = "UiPath 상담 분석 호출에 실패했습니다."
    if status in (401, 403):
        message = "UiPath 인증 또는 모델 접근 권한을 확인해 주세요."
    if status in (408, 504, 524) or any(isinstance(error, (
        APITimeoutError, TimeoutError, TimeoutException,
    )) for error in errors):
        message, reason = "UiPath 응답 시간이 초과되었습니다.", "timeout"
    elif status is not None and status >= 400:
        reason = messages.get(status, "gateway_server_error" if status >= 500 else "gateway_http_error")
    else:
        reason = "sdk_error"
        for error_type, code in (
            (LengthFinishReasonError, "model_output_truncated"),
            (ValidationError, "pydantic_validation"),
            (JSONDecodeError, "invalid_response_json"),
            (OutputParserException, "structured_output_parser"),
            (RequestError, "transport_error"),
        ):
            if any(isinstance(error, error_type) for error in errors):
                reason = code
                break
        if reason in ("model_output_truncated", "pydantic_validation", "invalid_response_json",
                      "structured_output_parser"):
            message = "UiPath의 구조화 응답을 검증할 수 없습니다."
    return UiPathLLMError(message, stage=stage, reason=reason, status=status)
