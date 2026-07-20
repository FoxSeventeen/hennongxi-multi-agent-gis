"""Run one explicit real-provider planning smoke without exposing secrets."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import httpx
from hennongxi_contracts import ModelCallRecord, PlanStepKind
from pydantic import BaseModel, ConfigDict, Field

from hennongxi_master.llm import (
    LlmConfig,
    LlmConfigurationError,
    LlmPlanningAdapter,
    LlmPlanningError,
)

SMOKE_QUERY = "监测神农溪流域两期 NDVI 变化，核验质量并生成中文报告"


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _SuccessEvidence(_Evidence):
    ok: Literal[True] = True
    provider_origin_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str
    task_id: UUID
    plan_id: UUID
    started_at: str
    duration_ms: int = Field(ge=0)
    status: Literal["SUCCEEDED"]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_kinds: tuple[PlanStepKind, ...]


class _ProviderFailureEvidence(_Evidence):
    ok: Literal[False] = False
    provider_origin_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: str
    started_at: str
    duration_ms: int = Field(ge=0)
    status: Literal["FAILED"]
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class SmokeCommandResult:
    exit_code: int
    output: str


async def execute_smoke(
    *,
    environment: Mapping[str, str] | None = None,
    task_id: UUID | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SmokeCommandResult:
    """Execute one fixed-query provider call and return JSON-safe evidence."""

    try:
        config = LlmConfig.from_environment(environment)
    except LlmConfigurationError as error:
        return _result(2, {"ok": False, "error_code": error.code})

    provider_origin_sha256 = sha256(config.base_url.encode()).hexdigest()
    try:
        async with httpx.AsyncClient(transport=transport) as client:
            plan = await LlmPlanningAdapter(config, client).create_plan(
                task_id=task_id or uuid4(),
                query=SMOKE_QUERY,
            )
    except LlmPlanningError as error:
        failure_evidence = _failure_evidence(
            provider_origin_sha256=provider_origin_sha256,
            model_call=error.model_call,
            retryable=error.retryable,
        )
        return SmokeCommandResult(exit_code=1, output=failure_evidence.model_dump_json())
    except Exception:
        return _result(3, {"ok": False, "error_code": "LLM_SMOKE_INTERNAL_ERROR"})

    model_call = plan.model_call
    if model_call is None or model_call.response_sha256 is None:
        return _result(3, {"ok": False, "error_code": "LLM_SMOKE_INTERNAL_ERROR"})

    success_evidence = _SuccessEvidence(
        provider_origin_sha256=provider_origin_sha256,
        model=model_call.model,
        task_id=plan.task_id,
        plan_id=plan.plan_id,
        started_at=model_call.started_at.isoformat(),
        duration_ms=model_call.duration_ms,
        status="SUCCEEDED",
        input_tokens=model_call.input_tokens,
        output_tokens=model_call.output_tokens,
        response_sha256=model_call.response_sha256,
        step_kinds=tuple(step.kind for step in plan.steps),
    )
    return SmokeCommandResult(exit_code=0, output=success_evidence.model_dump_json())


def _failure_evidence(
    *,
    provider_origin_sha256: str,
    model_call: ModelCallRecord,
    retryable: bool,
) -> _ProviderFailureEvidence:
    return _ProviderFailureEvidence(
        provider_origin_sha256=provider_origin_sha256,
        model=model_call.model,
        started_at=model_call.started_at.isoformat(),
        duration_ms=model_call.duration_ms,
        status="FAILED",
        input_tokens=model_call.input_tokens,
        output_tokens=model_call.output_tokens,
        response_sha256=model_call.response_sha256,
        error_code=model_call.error_code or "LLM_SMOKE_INTERNAL_ERROR",
        retryable=retryable,
    )


def _result(exit_code: int, payload: dict[str, object]) -> SmokeCommandResult:
    return SmokeCommandResult(
        exit_code=exit_code,
        output=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def main() -> int:
    result = asyncio.run(execute_smoke())
    stream = sys.stdout if result.exit_code == 0 else sys.stderr
    print(result.output, file=stream)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
