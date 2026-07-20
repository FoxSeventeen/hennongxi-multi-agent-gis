"""Convert untrusted model JSON into the fixed local execution plan."""

from __future__ import annotations

from typing import Self
from uuid import UUID

from hennongxi_contracts import (
    ExecutionPlan,
    ModelCallRecord,
    ModelCallStatus,
    PlanSource,
    PlanStep,
    PlanStepKind,
)
from hennongxi_contracts.common import UtcDateTime
from hennongxi_contracts.plans import STEP_AGENT
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_PROVIDER_CONTENT_CHARS = 20_000


class LlmPlanValidationError(ValueError):
    """A sanitized model-output validation failure safe for logs and responses."""

    code = "LLM_PLAN_INVALID"

    def __init__(self) -> None:
        super().__init__("LLM plan failed schema validation")


class _ProviderPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: PlanStepKind
    title: str = Field(min_length=1, max_length=200)


class _ProviderPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: tuple[_ProviderPlanStep, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def require_fixed_step_sequence(self) -> Self:
        if tuple(step.kind for step in self.steps) != tuple(PlanStepKind):
            raise ValueError("provider plan must use the fixed step sequence")
        return self


def build_llm_execution_plan(
    *,
    task_id: UUID,
    plan_id: UUID,
    created_at: UtcDateTime,
    model_call: ModelCallRecord,
    provider_content: str,
) -> ExecutionPlan:
    """Validate the provider draft and add all executable fields locally."""

    if (
        not provider_content.strip()
        or len(provider_content) > MAX_PROVIDER_CONTENT_CHARS
        or model_call.status is not ModelCallStatus.SUCCEEDED
    ):
        raise LlmPlanValidationError()

    try:
        provider_plan = _ProviderPlan.model_validate_json(provider_content)
        steps = tuple(
            PlanStep(
                step_id=provider_step.kind.value,
                kind=provider_step.kind,
                agent=STEP_AGENT[provider_step.kind],
                order=order,
                title=provider_step.title,
                depends_on=(() if order == 1 else (provider_plan.steps[order - 2].kind.value,)),
            )
            for order, provider_step in enumerate(provider_plan.steps, start=1)
        )
        return ExecutionPlan(
            plan_id=plan_id,
            task_id=task_id,
            source=PlanSource.REAL_LLM,
            created_at=created_at,
            model_call=model_call,
            steps=steps,
        )
    except ValidationError:
        raise LlmPlanValidationError() from None
