"""Provider-compatible HTTP adapter for safe LLM planning."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Self
from uuid import UUID, uuid4

import httpx
from hennongxi_contracts import (
    ExecutionPlan,
    ModelCallRecord,
    ModelCallStatus,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from hennongxi_master.planning import (
    MAX_PROVIDER_CONTENT_CHARS,
    LlmPlanValidationError,
    build_llm_execution_plan,
)

MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024
MAX_OUTPUT_TOKENS = 800

SYSTEM_PROMPT = """你是神农溪生态监测规划器。只返回一个 JSON 对象，不要返回 Markdown。
对象必须只含 steps；steps 必须严格依次包含 prepare_data、analyze_ndvi_change、
evaluate_quality、publish_results。每项只能包含 kind 和简短中文 title。
不得输出命令、代码、SQL、URL、文件路径、Agent、顺序、依赖或标识符。"""

_ERROR_MESSAGES = {
    "LLM_TIMEOUT": "LLM provider request timed out",
    "LLM_AUTHENTICATION_FAILED": "LLM provider authentication failed",
    "LLM_RATE_LIMITED": "LLM provider rate limit exceeded",
    "LLM_PROVIDER_UNAVAILABLE": "LLM provider is unavailable",
    "LLM_PROVIDER_REJECTED": "LLM provider rejected the request",
    "LLM_RESPONSE_INVALID": "LLM provider response is invalid",
    "LLM_PLAN_INVALID": "LLM plan failed schema validation",
}


class LlmConfigurationError(ValueError):
    """A configuration failure that never echoes environment values."""

    code = "LLM_NOT_CONFIGURED"

    def __init__(self) -> None:
        super().__init__("LLM configuration is invalid or incomplete")


class LlmPlanningError(RuntimeError):
    """A sanitized provider failure with persistence-safe call metadata."""

    def __init__(
        self,
        *,
        code: str,
        retryable: bool,
        model_call: ModelCallRecord,
    ) -> None:
        self.code = code
        self.retryable = retryable
        self.model_call = model_call
        super().__init__(_ERROR_MESSAGES[code])


class LlmConfig(BaseModel):
    """Validated environment-only provider configuration with a redacted repr."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )

    api_key: SecretStr = Field(min_length=1, repr=False)
    base_url: str = Field(min_length=1, repr=False)
    model: str = Field(min_length=1, max_length=200)
    timeout_seconds: float = Field(default=30, gt=0, le=120)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL as error:
            raise ValueError("LLM_BASE_URL is invalid") from error
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("LLM_BASE_URL must be an HTTP origin without credentials")
        return str(url).rstrip("/")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Self:
        values = os.environ if environment is None else environment
        try:
            return cls.model_validate(
                {
                    "api_key": values.get("LLM_API_KEY"),
                    "base_url": values.get("LLM_BASE_URL"),
                    "model": values.get("LLM_MODEL"),
                    "timeout_seconds": values.get("LLM_TIMEOUT_SECONDS", "30"),
                }
            )
        except ValidationError:
            raise LlmConfigurationError() from None


class _ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str = Field(min_length=1, max_length=MAX_PROVIDER_CONTENT_CHARS)


class _ProviderChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _ProviderMessage


class _ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)


class _ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choices: tuple[_ProviderChoice, ...] = Field(min_length=1)
    usage: _ProviderUsage | None = None


class _ProviderResponseTooLarge(ValueError):
    pass


@dataclass(slots=True, repr=False)
class LlmPlanningAdapter:
    """Call one provider endpoint and return only a validated execution plan."""

    config: LlmConfig
    client: httpx.AsyncClient

    async def create_plan(self, *, task_id: UUID, query: str) -> ExecutionPlan:
        plan_id = uuid4()
        started_at = datetime.now(UTC)
        timer_started = time.monotonic()

        try:
            async with self.client.stream(
                "POST",
                f"{self.config.base_url}/chat/completions",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
                    "X-Client-Request-Id": str(plan_id),
                },
                json=_request_payload(self.config.model, query),
                timeout=httpx.Timeout(self.config.timeout_seconds),
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    code, retryable = _map_status(response.status_code)
                    raise self._error(
                        code=code,
                        retryable=retryable,
                        started_at=started_at,
                        timer_started=timer_started,
                    )
                response_body = await _read_bounded_response(response)

            response_digest = sha256(response_body).hexdigest()
            try:
                provider_response = _ProviderResponse.model_validate_json(response_body)
            except ValidationError:
                raise self._error(
                    code="LLM_RESPONSE_INVALID",
                    retryable=True,
                    started_at=started_at,
                    timer_started=timer_started,
                    response_sha256=response_digest,
                ) from None

            model_call = self._model_call(
                status=ModelCallStatus.SUCCEEDED,
                started_at=started_at,
                timer_started=timer_started,
                response_sha256=response_digest,
                input_tokens=(
                    provider_response.usage.prompt_tokens if provider_response.usage else None
                ),
                output_tokens=(
                    provider_response.usage.completion_tokens if provider_response.usage else None
                ),
            )
            try:
                return build_llm_execution_plan(
                    task_id=task_id,
                    plan_id=plan_id,
                    created_at=datetime.now(UTC),
                    model_call=model_call,
                    provider_content=provider_response.choices[0].message.content,
                )
            except LlmPlanValidationError:
                raise self._error(
                    code="LLM_PLAN_INVALID",
                    retryable=False,
                    started_at=started_at,
                    timer_started=timer_started,
                    response_sha256=response_digest,
                    input_tokens=model_call.input_tokens,
                    output_tokens=model_call.output_tokens,
                ) from None
        except LlmPlanningError:
            raise
        except httpx.TimeoutException:
            raise self._error(
                code="LLM_TIMEOUT",
                retryable=True,
                started_at=started_at,
                timer_started=timer_started,
            ) from None
        except httpx.RequestError:
            raise self._error(
                code="LLM_PROVIDER_UNAVAILABLE",
                retryable=True,
                started_at=started_at,
                timer_started=timer_started,
            ) from None
        except _ProviderResponseTooLarge:
            raise self._error(
                code="LLM_RESPONSE_INVALID",
                retryable=True,
                started_at=started_at,
                timer_started=timer_started,
            ) from None

    def _error(
        self,
        *,
        code: str,
        retryable: bool,
        started_at: datetime,
        timer_started: float,
        response_sha256: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> LlmPlanningError:
        return LlmPlanningError(
            code=code,
            retryable=retryable,
            model_call=self._model_call(
                status=ModelCallStatus.FAILED,
                started_at=started_at,
                timer_started=timer_started,
                response_sha256=response_sha256,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_code=code,
            ),
        )

    def _model_call(
        self,
        *,
        status: ModelCallStatus,
        started_at: datetime,
        timer_started: float,
        response_sha256: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        error_code: str | None = None,
    ) -> ModelCallRecord:
        duration_ms = max(0, round((time.monotonic() - timer_started) * 1000))
        return ModelCallRecord(
            model=self.config.model,
            started_at=started_at,
            duration_ms=duration_ms,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_sha256=response_sha256,
            error_code=error_code,
        )


def _request_payload(model: str, query: str) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "n": 1,
    }


def _map_status(status_code: int) -> tuple[str, bool]:
    if status_code in {401, 403}:
        return "LLM_AUTHENTICATION_FAILED", False
    if status_code == 429:
        return "LLM_RATE_LIMITED", True
    if 500 <= status_code <= 599:
        return "LLM_PROVIDER_UNAVAILABLE", True
    return "LLM_PROVIDER_REJECTED", False


async def _read_bounded_response(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_PROVIDER_RESPONSE_BYTES:
            raise _ProviderResponseTooLarge
        body.extend(chunk)
    return bytes(body)
