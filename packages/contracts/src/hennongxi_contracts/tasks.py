"""Public task creation, query, retry, and readiness response models."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from hennongxi_contracts.artifacts import ArtifactRef
from hennongxi_contracts.common import (
    AgentName,
    ContractModel,
    NonBlankText,
    StepId,
    UtcDateTime,
)
from hennongxi_contracts.errors import StructuredError
from hennongxi_contracts.plans import ExecutionPlan, PlanStepKind
from hennongxi_contracts.state import TaskStatus


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class CreateTaskRequest(ContractModel):
    query: NonBlankText


class TaskAcceptedResponse(ContractModel):
    task_id: UUID
    status: TaskStatus
    created_at: UtcDateTime

    @model_validator(mode="after")
    def require_pending_status(self) -> Self:
        if self.status is not TaskStatus.PENDING:
            raise ValueError("newly accepted task must have PENDING status")
        return self


class RetryAcceptedResponse(ContractModel):
    task_id: UUID
    attempt: int = Field(ge=2)
    status: TaskStatus
    accepted_at: UtcDateTime

    @model_validator(mode="after")
    def require_pending_status(self) -> Self:
        if self.status is not TaskStatus.PENDING:
            raise ValueError("accepted retry must have PENDING status")
        return self


class TaskStep(ContractModel):
    step_id: StepId
    kind: PlanStepKind
    agent: AgentName
    attempt: int = Field(ge=1)
    status: StepStatus
    progress: int = Field(ge=0, le=100)
    started_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None
    elapsed_ms: int | None = Field(default=None, ge=0)
    error: StructuredError | None = None
    artifacts: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def require_terminal_evidence(self) -> Self:
        if self.status in {StepStatus.COMPLETED, StepStatus.SKIPPED} and (
            self.progress != 100 or self.completed_at is None or self.error is not None
        ):
            raise ValueError(
                "completed or skipped step requires 100 progress, completed_at, and no error"
            )
        if self.status is StepStatus.FAILED and (self.completed_at is None or self.error is None):
            raise ValueError("failed step requires completed_at and structured error")
        return self


class TaskResponse(ContractModel):
    task_id: UUID
    query: NonBlankText
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    current_attempt: int = Field(ge=1)
    correlation_id: UUID
    created_at: UtcDateTime
    updated_at: UtcDateTime
    plan: ExecutionPlan | None = None
    steps: tuple[TaskStep, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    last_error: StructuredError | None = None

    @model_validator(mode="after")
    def require_consistent_terminal_state(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.status is TaskStatus.COMPLETED and (
            self.progress != 100 or self.last_error is not None
        ):
            raise ValueError("completed task requires 100 progress and no error")
        if self.status is TaskStatus.FAILED and self.last_error is None:
            raise ValueError("failed task requires last_error")
        if self.plan is not None and self.plan.task_id != self.task_id:
            raise ValueError("plan must belong to the same task")
        if any(artifact.task_id != self.task_id for artifact in self.artifacts):
            raise ValueError("artifacts must belong to the same task")
        if any(
            artifact.task_id != self.task_id or artifact.attempt != step.attempt
            for step in self.steps
            for artifact in step.artifacts
        ):
            raise ValueError("step artifacts must belong to the same task and attempt")
        return self
