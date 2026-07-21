"""Bounded, contract-validated HTTP calls from Master to private Agents."""

from __future__ import annotations

import os
from collections.abc import Mapping
from time import monotonic
from typing import cast
from uuid import UUID

import httpx
import structlog
from hennongxi_contracts import (
    AgentName,
    AnalysisRunCommand,
    AnalysisRunResult,
    DataPrepareCommand,
    DataPrepareResult,
    ErrorCode,
    ErrorResponse,
    PublisherPublishCommand,
    PublisherPublishResult,
    QualityEvaluateCommand,
    QualityEvaluateResult,
    StructuredError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_AGENT_RESPONSE_BYTES = 512 * 1024
_LOGGER = structlog.get_logger("hennongxi.master.agent_client")

type AgentCommand = (
    DataPrepareCommand | AnalysisRunCommand | QualityEvaluateCommand | PublisherPublishCommand
)
type AgentResult = (
    DataPrepareResult | AnalysisRunResult | QualityEvaluateResult | PublisherPublishResult
)


class AgentClientConfig(BaseModel):
    """Validated operator-controlled origins and bounded network timeouts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )

    data_base_url: str
    analysis_base_url: str
    quality_base_url: str
    publisher_base_url: str
    connect_timeout_seconds: float = Field(default=5, gt=0, le=30)
    read_timeout_seconds: float = Field(default=300, gt=0, le=900)

    @field_validator(
        "data_base_url",
        "analysis_base_url",
        "quality_base_url",
        "publisher_base_url",
    )
    @classmethod
    def require_clean_http_origin(cls, value: str) -> str:
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL as error:
            raise ValueError("Agent base URL must be a clean HTTP origin") from error
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Agent base URL must be a clean HTTP origin")
        return str(url).rstrip("/")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> AgentClientConfig:
        values = os.environ if environment is None else environment
        return cls.model_validate(
            {
                "data_base_url": values.get("DATA_AGENT_BASE_URL", "http://data-agent:8001"),
                "analysis_base_url": values.get(
                    "ANALYSIS_AGENT_BASE_URL", "http://analysis-agent:8002"
                ),
                "quality_base_url": values.get(
                    "QUALITY_AGENT_BASE_URL", "http://quality-agent:8003"
                ),
                "publisher_base_url": values.get(
                    "PUBLISHER_AGENT_BASE_URL", "http://publisher-agent:8004"
                ),
                "connect_timeout_seconds": values.get("AGENT_CONNECT_TIMEOUT_SECONDS", "5"),
                "read_timeout_seconds": values.get("AGENT_READ_TIMEOUT_SECONDS", "300"),
            }
        )


class AgentCallError(RuntimeError):
    """Sanitized Agent failure safe for logs, persistence, and public task state."""

    def __init__(
        self,
        *,
        agent: AgentName,
        step_id: str,
        error: StructuredError,
        elapsed_ms: int,
    ) -> None:
        self.agent = agent
        self.step_id = step_id
        self.error = error
        self.elapsed_ms = elapsed_ms
        super().__init__(f"{agent.value} Agent call failed ({error.code.value})")


class AgentHttpClient:
    """Invoke fixed private routes through one lifespan-scoped HTTPX client."""

    def __init__(self, config: AgentClientConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    async def prepare_data(self, command: DataPrepareCommand) -> DataPrepareResult:
        result = await self._post(
            agent=AgentName.DATA,
            base_url=self._config.data_base_url,
            path="/internal/v1/data/prepare",
            command=command,
            response_model=DataPrepareResult,
        )
        return cast(DataPrepareResult, result)

    async def run_analysis(
        self,
        command: AnalysisRunCommand,
        *,
        idempotency_key: UUID,
    ) -> AnalysisRunResult:
        result = await self._post(
            agent=AgentName.ANALYSIS,
            base_url=self._config.analysis_base_url,
            path="/internal/v1/analysis/run",
            command=command,
            response_model=AnalysisRunResult,
            idempotency_key=idempotency_key,
        )
        return cast(AnalysisRunResult, result)

    async def evaluate_quality(
        self,
        command: QualityEvaluateCommand,
        *,
        idempotency_key: UUID,
    ) -> QualityEvaluateResult:
        result = await self._post(
            agent=AgentName.QUALITY,
            base_url=self._config.quality_base_url,
            path="/internal/v1/quality/evaluate",
            command=command,
            response_model=QualityEvaluateResult,
            idempotency_key=idempotency_key,
        )
        return cast(QualityEvaluateResult, result)

    async def publish_results(
        self,
        command: PublisherPublishCommand,
        *,
        idempotency_key: UUID,
    ) -> PublisherPublishResult:
        result = await self._post(
            agent=AgentName.PUBLISHER,
            base_url=self._config.publisher_base_url,
            path="/internal/v1/publisher/publish",
            command=command,
            response_model=PublisherPublishResult,
            idempotency_key=idempotency_key,
        )
        return cast(PublisherPublishResult, result)

    async def _post(
        self,
        *,
        agent: AgentName,
        base_url: str,
        path: str,
        command: AgentCommand,
        response_model: type[AgentResult],
        idempotency_key: UUID | None = None,
    ) -> AgentResult:
        started = monotonic()
        fields = {
            "agent": agent.value,
            "task_id": str(command.task_id),
            "step_id": command.step_id,
            "attempt": command.attempt,
            "correlation_id": str(command.correlation_id),
        }
        _LOGGER.info("agent_call_started", **fields)
        headers = {
            "Accept": "application/json",
            "X-Correlation-ID": str(command.correlation_id),
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = str(idempotency_key)

        try:
            async with self._client.stream(
                "POST",
                f"{base_url}{path}",
                headers=headers,
                json=command.model_dump(mode="json", exclude_none=True),
                follow_redirects=False,
            ) as response:
                body = await _read_bounded_body(
                    response,
                    agent=agent,
                    step_id=command.step_id,
                    started=started,
                )
                status_code = response.status_code
            if not 200 <= status_code < 300:
                raise _remote_failure(
                    agent=agent,
                    step_id=command.step_id,
                    body=body,
                    elapsed_ms=_elapsed_ms(started),
                )
            try:
                result = response_model.model_validate_json(body)
            except ValidationError:
                raise _invalid_response(agent, command.step_id, _elapsed_ms(started)) from None
            if (
                result.task_id != command.task_id
                or result.step_id != command.step_id
                or result.attempt != command.attempt
                or result.correlation_id != command.correlation_id
            ):
                raise _invalid_response(agent, command.step_id, _elapsed_ms(started))
        except AgentCallError as call_error:
            _LOGGER.warning(
                "agent_call_failed",
                **fields,
                elapsed_ms=call_error.elapsed_ms,
                error_code=call_error.error.code.value,
                retryable=call_error.error.retryable,
            )
            raise
        except httpx.TimeoutException:
            timeout_error = _transport_failure(
                agent, command.step_id, _elapsed_ms(started), timeout=True
            )
            _LOGGER.warning(
                "agent_call_failed",
                **fields,
                elapsed_ms=timeout_error.elapsed_ms,
                error_code=timeout_error.error.code.value,
                retryable=True,
            )
            raise timeout_error from None
        except httpx.RequestError:
            request_error = _transport_failure(
                agent, command.step_id, _elapsed_ms(started), timeout=False
            )
            _LOGGER.warning(
                "agent_call_failed",
                **fields,
                elapsed_ms=request_error.elapsed_ms,
                error_code=request_error.error.code.value,
                retryable=True,
            )
            raise request_error from None

        elapsed_ms = _elapsed_ms(started)
        _LOGGER.info("agent_call_completed", **fields, elapsed_ms=elapsed_ms)
        return result


def create_agent_async_client(config: AgentClientConfig) -> httpx.AsyncClient:
    """Create one bounded client intended to live for the Master lifespan."""

    # Sources: https://www.python-httpx.org/async/#opening-and-closing-clients
    # and https://www.python-httpx.org/advanced/timeouts/#fine-tuning-the-configuration
    timeout = httpx.Timeout(
        connect=config.connect_timeout_seconds,
        read=config.read_timeout_seconds,
        write=10,
        pool=5,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        follow_redirects=False,
        trust_env=False,
    )


async def _read_bounded_body(
    response: httpx.Response,
    *,
    agent: AgentName,
    step_id: str,
    started: float,
) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_AGENT_RESPONSE_BYTES:
            raise _invalid_response(agent, step_id, _elapsed_ms(started))
        body.extend(chunk)
    return bytes(body)


def _remote_failure(
    *,
    agent: AgentName,
    step_id: str,
    body: bytes,
    elapsed_ms: int,
) -> AgentCallError:
    try:
        remote_error = ErrorResponse.model_validate_json(body).error
    except ValidationError:
        return _invalid_response(agent, step_id, elapsed_ms)
    return AgentCallError(
        agent=agent,
        step_id=step_id,
        error=StructuredError(
            code=remote_error.code,
            message=f"{_agent_label(agent)}返回了失败结果",
            retryable=remote_error.retryable,
        ),
        elapsed_ms=elapsed_ms,
    )


def _invalid_response(agent: AgentName, step_id: str, elapsed_ms: int) -> AgentCallError:
    return AgentCallError(
        agent=agent,
        step_id=step_id,
        error=StructuredError(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"{_agent_label(agent)}响应不符合内部契约",
            retryable=True,
        ),
        elapsed_ms=elapsed_ms,
    )


def _transport_failure(
    agent: AgentName,
    step_id: str,
    elapsed_ms: int,
    *,
    timeout: bool,
) -> AgentCallError:
    reason = "调用超时" if timeout else "暂时不可用"
    return AgentCallError(
        agent=agent,
        step_id=step_id,
        error=StructuredError(
            code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            message=f"{_agent_label(agent)}{reason}",
            retryable=True,
        ),
        elapsed_ms=elapsed_ms,
    )


def _agent_label(agent: AgentName) -> str:
    return {
        AgentName.DATA: "Data Agent",
        AgentName.ANALYSIS: "Analysis Agent",
        AgentName.QUALITY: "Quality Agent",
        AgentName.PUBLISHER: "Publisher Agent",
        AgentName.MASTER: "Agent",
    }[agent]


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
