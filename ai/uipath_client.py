"""Structured consultation responses through the UiPath LLM Gateway."""

import os
from json import JSONDecodeError
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from httpx import TimeoutException
from langchain_core.exceptions import OutputParserException
from openai import APITimeoutError, LengthFinishReasonError
from pydantic import BaseModel, ValidationError
from uipath_langchain.chat.models import UiPathAzureChatOpenAI

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

T = TypeVar("T", bound=BaseModel)
DEFAULT_MODEL = "gpt-4.1-mini-2025-04-14"


class UiPathLLMError(RuntimeError):
    """Sanitized error suitable for logging and showing to an operator."""


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
    except ValidationError:
        raise UiPathLLMError("UiPath 인증 또는 연결 설정을 확인해 주세요.") from None
    except Exception as exc:
        raise _gateway_error(exc) from None

    try:
        response = llm.with_structured_output(response_schema).invoke([
            ("system", system_prompt),
            ("human", user_prompt),
        ])
    except (JSONDecodeError, LengthFinishReasonError, OutputParserException, ValidationError):
        raise UiPathLLMError("UiPath의 구조화 응답을 검증할 수 없습니다.") from None
    except Exception as exc:
        raise _gateway_error(exc) from None

    if not isinstance(response, response_schema):
        raise UiPathLLMError("UiPath의 구조화 응답을 검증할 수 없습니다.")

    return response


def _gateway_error(exc: Exception) -> UiPathLLMError:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)

    if status in (401, 403):
        return UiPathLLMError("UiPath 인증 또는 모델 접근 권한을 확인해 주세요.")
    if isinstance(exc, (APITimeoutError, TimeoutError, TimeoutException)) or status in (408, 504, 524):
        return UiPathLLMError("UiPath 응답 시간이 초과되었습니다.")
    return UiPathLLMError("UiPath 상담 분석 호출에 실패했습니다.")
