"""The fixed, schema-validated ecological monitoring plan."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from hennongxi_contracts.common import (
    AgentName,
    ContractModel,
    Sha256Digest,
    ShortText,
    StepId,
    UtcDateTime,
)


class PlanStepKind(StrEnum):
    PREPARE_DATA = "prepare_data"
    ANALYZE_NDVI_CHANGE = "analyze_ndvi_change"
    EVALUATE_QUALITY = "evaluate_quality"
    PUBLISH_RESULTS = "publish_results"


STEP_AGENT: dict[PlanStepKind, AgentName] = {
    PlanStepKind.PREPARE_DATA: AgentName.DATA,
    PlanStepKind.ANALYZE_NDVI_CHANGE: AgentName.ANALYSIS,
    PlanStepKind.EVALUATE_QUALITY: AgentName.QUALITY,
    PlanStepKind.PUBLISH_RESULTS: AgentName.PUBLISHER,
}


class PlanSource(StrEnum):
    REAL_LLM = "REAL_LLM"
    BUILTIN_RECOVERY = "BUILTIN_RECOVERY"


class ModelCallStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ModelCallRecord(ContractModel):
    model: ShortText
    started_at: UtcDateTime
    duration_ms: int = Field(ge=0)
    status: ModelCallStatus
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    response_sha256: Sha256Digest | None = None
    error_code: ShortText | None = None

    @model_validator(mode="after")
    def require_status_evidence(self) -> Self:
        if self.status is ModelCallStatus.SUCCEEDED and self.response_sha256 is None:
            raise ValueError("successful model call requires response_sha256")
        if self.status is ModelCallStatus.FAILED and self.error_code is None:
            raise ValueError("failed model call requires error_code")
        return self


class PlanStep(ContractModel):
    step_id: StepId
    kind: PlanStepKind
    agent: AgentName
    order: int = Field(ge=1, le=4)
    title: ShortText
    depends_on: tuple[StepId, ...] = ()

    @model_validator(mode="after")
    def require_approved_agent(self) -> Self:
        expected = STEP_AGENT[self.kind]
        if self.agent is not expected:
            raise ValueError(f"{self.kind} must run on agent {expected}")
        return self


class ExecutionPlan(ContractModel):
    plan_id: UUID
    task_id: UUID
    source: PlanSource
    created_at: UtcDateTime
    model_call: ModelCallRecord | None = None
    steps: tuple[PlanStep, ...]

    @model_validator(mode="after")
    def require_fixed_plan(self) -> Self:
        expected_kinds = tuple(PlanStepKind)
        if tuple(step.kind for step in self.steps) != expected_kinds:
            raise ValueError("plan must use the fixed ecological-monitoring sequence")
        if tuple(step.order for step in self.steps) != (1, 2, 3, 4):
            raise ValueError("plan step order must be contiguous from 1 through 4")

        step_ids = tuple(step.step_id for step in self.steps)
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step_id values must be unique")
        for index, step in enumerate(self.steps):
            expected_dependencies = () if index == 0 else (self.steps[index - 1].step_id,)
            if step.depends_on != expected_dependencies:
                raise ValueError("plan steps must form the approved dependency chain")

        if self.source is PlanSource.REAL_LLM and (
            self.model_call is None or self.model_call.status is not ModelCallStatus.SUCCEEDED
        ):
            raise ValueError("REAL_LLM plan requires succeeded model_call evidence")
        if self.source is PlanSource.BUILTIN_RECOVERY and self.model_call is not None:
            raise ValueError("BUILTIN_RECOVERY plan cannot claim model_call evidence")
        return self
