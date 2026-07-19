"""Ordered durable event records used by SSE and polling reconstruction."""

from __future__ import annotations

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
from hennongxi_contracts.state import TaskStatus


class TaskEvent(ContractModel):
    sequence: int = Field(ge=1)
    task_id: UUID
    step_id: StepId
    attempt: int = Field(ge=1)
    correlation_id: UUID
    agent: AgentName
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    message: NonBlankText
    elapsed_ms: int = Field(ge=0)
    occurred_at: UtcDateTime
    error: StructuredError | None = None
    artifacts: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        if any(
            artifact.task_id != self.task_id or artifact.attempt != self.attempt
            for artifact in self.artifacts
        ):
            raise ValueError("event artifacts must belong to the same task and attempt")
        return self
