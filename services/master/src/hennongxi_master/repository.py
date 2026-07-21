"""Async PostGIS repository for durable workflow state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Self, overload
from uuid import UUID, uuid4

from hennongxi_contracts import (
    AgentName,
    AnalysisRunResult,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    CreateTaskRequest,
    DataPrepareResult,
    ErrorCode,
    ExecutionPlan,
    ModelCallRecord,
    ModelCallStatus,
    PlanSource,
    PlanStepKind,
    PublisherPublishResult,
    QualityEvaluateResult,
    RetryAcceptedResponse,
    StepStatus,
    StructuredError,
    TaskEvent,
    TaskResponse,
    TaskStatus,
    TaskStep,
    require_task_transition,
)
from hennongxi_contracts.common import NonBlankText, StepId, UtcDateTime
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class RepositoryInput(BaseModel):
    """Strict base class for values crossing into the persistence boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


type StepOutput = (
    DataPrepareResult | AnalysisRunResult | QualityEvaluateResult | PublisherPublishResult
)

_INTERRUPTED_STEP_BY_STATUS: dict[TaskStatus, str] = {
    TaskStatus.PLANNING: "planning",
    TaskStatus.DATA_PREPARING: "prepare_data",
    TaskStatus.ANALYZING: "analyze_ndvi_change",
    TaskStatus.QUALITY_CHECKING: "evaluate_quality",
    TaskStatus.PUBLISHING: "publish_results",
}


class WatershedCreate(RepositoryInput):
    watershed_id: UUID
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{0,99}$")
    name: str = Field(min_length=1, max_length=200)
    geometry: dict[str, Any]
    source_metadata: dict[str, Any]
    created_at: UtcDateTime

    @field_validator("geometry")
    @classmethod
    def require_polygon_geometry(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") not in {"Polygon", "MultiPolygon"} or "coordinates" not in value:
            raise ValueError("watershed geometry must be a Polygon or MultiPolygon fragment")
        _json(value)
        return value

    @field_validator("source_metadata")
    @classmethod
    def require_json_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        _json(value)
        return value


class ArtifactCreate(RepositoryInput):
    artifact: ArtifactRef
    step_id: StepId
    storage_key: str = Field(
        min_length=1,
        max_length=500,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )

    @field_validator("storage_key")
    @classmethod
    def reject_unsafe_storage_key(cls, value: str) -> str:
        if ".." in value or "\\" in value:
            raise ValueError("storage_key must be a safe relative key")
        return value


class TransitionCreate(RepositoryInput):
    task_id: UUID
    attempt: int = Field(ge=1)
    step_id: StepId
    agent: AgentName
    target_status: TaskStatus
    progress: int = Field(ge=0, le=100)
    message: NonBlankText
    elapsed_ms: int = Field(ge=0)
    occurred_at: UtcDateTime
    error: StructuredError | None = None
    step_status: StepStatus | None = None
    step_progress: int | None = Field(default=None, ge=0, le=100)
    step_started_at: UtcDateTime | None = None
    step_completed_at: UtcDateTime | None = None
    artifact_ids: tuple[UUID, ...] = ()
    step_output: StepOutput | None = None

    @model_validator(mode="after")
    def require_consistent_evidence(self) -> Self:
        if self.target_status is TaskStatus.FAILED and self.error is None:
            raise ValueError("FAILED transition requires a structured error")
        if self.target_status is TaskStatus.COMPLETED and (
            self.progress != 100 or self.error is not None
        ):
            raise ValueError("COMPLETED transition requires 100 progress and no error")
        if self.step_status is None:
            if any(
                value is not None
                for value in (
                    self.step_progress,
                    self.step_started_at,
                    self.step_completed_at,
                )
            ):
                raise ValueError("step evidence requires step_status")
            if self.step_output is not None:
                raise ValueError("step output requires completed step evidence")
            return self
        if self.step_progress is None:
            raise ValueError("step_status requires step_progress")
        if self.step_status in {StepStatus.COMPLETED, StepStatus.SKIPPED} and (
            self.step_progress != 100 or self.step_completed_at is None or self.error is not None
        ):
            raise ValueError(
                "completed or skipped step requires 100 progress and completion evidence"
            )
        if self.step_status is StepStatus.FAILED and (
            self.step_completed_at is None or self.error is None
        ):
            raise ValueError("failed step requires completion time and structured error")
        if self.step_output is not None and (
            self.step_status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}
            or self.step_output.task_id != self.task_id
            or self.step_output.attempt != self.attempt
            or self.step_output.step_id != self.step_id
        ):
            raise ValueError("step output must match terminal task, attempt, and step evidence")
        return self


class ProgressCreate(TransitionCreate):
    """A durable progress event that must keep the task in its current active state."""

    @model_validator(mode="after")
    def require_nonterminal_step_progress(self) -> Self:
        if self.target_status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            raise ValueError("same-state progress requires an active task status")
        if self.step_status not in {StepStatus.RUNNING, StepStatus.COMPLETED}:
            raise ValueError("same-state progress requires a running or completed step")
        if self.step_status is StepStatus.RUNNING and self.artifact_ids:
            raise ValueError("running step progress cannot publish artifacts")
        return self


class WorkerClaimRequest(RepositoryInput):
    worker_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    claimed_at: UtcDateTime
    lease_seconds: int = Field(ge=1, le=3600)


class WorkerLeaseRenewal(RepositoryInput):
    heartbeat_at: UtcDateTime
    lease_seconds: int = Field(ge=1, le=3600)


class WorkerClaim(RepositoryInput):
    task_id: UUID
    attempt: int = Field(ge=1)
    worker_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    claimed_at: UtcDateTime
    heartbeat_at: UtcDateTime
    lease_expires_at: UtcDateTime
    released_at: UtcDateTime | None = None


@dataclass(frozen=True, slots=True)
class RetryAttemptResult:
    response: RetryAcceptedResponse
    event: TaskEvent | None
    created: bool


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    """Validated durable evidence available to one retry attempt."""

    source_attempt: int
    resume_from_step_id: str
    plan: ExecutionPlan | None
    data: DataPrepareResult | None
    analysis: AnalysisRunResult | None
    quality: QualityEvaluateResult | None


@dataclass(frozen=True, slots=True)
class InterruptedRecoveryResult:
    """One atomically failed interrupted attempt and its queued successor."""

    task_id: UUID
    interrupted_attempt: int
    retry_attempt: int
    resume_from_step_id: str


class RepositoryNotFound(LookupError):
    """Raised when a requested durable record does not exist."""


class RepositoryConflict(RuntimeError):
    """Raised when durable state changed incompatibly with a requested operation."""


class TaskRepository:
    """One-session-per-operation repository backed by SQLAlchemy asyncio."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        # Source: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
        # async_sessionmaker.begin() commits on success and rolls back on exceptions.
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, database_url: str) -> TaskRepository:
        return cls(create_async_engine(database_url, pool_pre_ping=True))

    async def dispose(self) -> None:
        await self._engine.dispose()

    async def create_watershed(self, value: WatershedCreate) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO watersheds "
                    "(watershed_id, slug, name, source_metadata, created_at, geometry) "
                    "VALUES (:watershed_id, :slug, :name, CAST(:source_metadata AS jsonb), "
                    ":created_at, "
                    "ST_Multi(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326))))"
                ),
                {
                    "watershed_id": value.watershed_id,
                    "slug": value.slug,
                    "name": value.name,
                    "source_metadata": _json(value.source_metadata),
                    "created_at": value.created_at,
                    "geometry": _json(value.geometry),
                },
            )

    async def ensure_watershed(self, value: WatershedCreate) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO watersheds "
                    "(watershed_id, slug, name, source_metadata, created_at, geometry) "
                    "VALUES (:watershed_id, :slug, :name, CAST(:source_metadata AS jsonb), "
                    ":created_at, "
                    "ST_Multi(ST_Force2D(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)))) "
                    "ON CONFLICT (watershed_id) DO NOTHING"
                ),
                {
                    "watershed_id": value.watershed_id,
                    "slug": value.slug,
                    "name": value.name,
                    "source_metadata": _json(value.source_metadata),
                    "created_at": value.created_at,
                    "geometry": _json(value.geometry),
                },
            )

    async def get_watershed_id_by_slug(self, slug: str) -> UUID | None:
        async with self._sessions() as session:
            watershed_id = await session.scalar(
                text("SELECT watershed_id FROM watersheds WHERE slug = :slug"),
                {"slug": slug},
            )
        return UUID(str(watershed_id)) if watershed_id is not None else None

    async def create_task(
        self,
        *,
        task_id: UUID,
        correlation_id: UUID,
        watershed_id: UUID,
        request: CreateTaskRequest,
        created_at: UtcDateTime,
    ) -> TaskResponse:
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO tasks "
                    "(task_id, watershed_id, query, status, progress, current_attempt, "
                    "correlation_id, created_at, updated_at) "
                    "VALUES (:task_id, :watershed_id, :query, 'PENDING', 0, 1, "
                    ":correlation_id, :created_at, :created_at)"
                ),
                {
                    "task_id": task_id,
                    "watershed_id": watershed_id,
                    "query": request.query,
                    "correlation_id": correlation_id,
                    "created_at": created_at,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO attempts (task_id, attempt, status, created_at) "
                    "VALUES (:task_id, 1, 'PENDING', :created_at)"
                ),
                {"task_id": task_id, "created_at": created_at},
            )
        result = await self.get_task(task_id)
        if result is None:
            raise RuntimeError("created task could not be reconstructed")
        return result

    async def retry_failed_task(
        self,
        task_id: UUID,
        *,
        accepted_at: UtcDateTime,
    ) -> RetryAttemptResult:
        """Atomically create one retry attempt or return its in-flight duplicate."""

        accepted_at = _UTC_DATETIME.validate_python(accepted_at)
        async with self._sessions.begin() as session:
            task = (
                (
                    await session.execute(
                        text(
                            "SELECT status, current_attempt, correlation_id FROM tasks "
                            "WHERE task_id = :task_id FOR UPDATE"
                        ),
                        {"task_id": task_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if task is None:
                raise RepositoryNotFound("task does not exist")

            current_attempt = int(task["current_attempt"])
            current_status = TaskStatus(str(task["status"]))
            attempt = (
                (
                    await session.execute(
                        text(
                            "SELECT created_at, resume_from_step_id FROM attempts "
                            "WHERE task_id = :task_id AND attempt = :attempt"
                        ),
                        {"task_id": task_id, "attempt": current_attempt},
                    )
                )
                .mappings()
                .one()
            )
            resume_from_step_id = attempt["resume_from_step_id"]
            if current_status is not TaskStatus.FAILED:
                if (
                    current_attempt >= 2
                    and resume_from_step_id is not None
                    and current_status is not TaskStatus.COMPLETED
                ):
                    return RetryAttemptResult(
                        response=RetryAcceptedResponse(
                            task_id=task_id,
                            attempt=current_attempt,
                            status=TaskStatus.PENDING,
                            accepted_at=attempt["created_at"],
                        ),
                        event=None,
                        created=False,
                    )
                raise RepositoryConflict("only failed tasks can be retried")

            failed_step_id = await session.scalar(
                text(
                    "SELECT step_id FROM events WHERE task_id = :task_id "
                    "AND attempt = :attempt AND status = 'FAILED' "
                    "ORDER BY sequence DESC LIMIT 1"
                ),
                {"task_id": task_id, "attempt": current_attempt},
            )
            if failed_step_id is None:
                raise RepositoryConflict("failed task has no durable failure checkpoint")

            next_attempt = current_attempt + 1
            await session.execute(
                text(
                    "INSERT INTO attempts "
                    "(task_id, attempt, status, resume_from_step_id, created_at) "
                    "VALUES (:task_id, :attempt, 'PENDING', :resume_from_step_id, :accepted_at)"
                ),
                {
                    "task_id": task_id,
                    "attempt": next_attempt,
                    "resume_from_step_id": str(failed_step_id),
                    "accepted_at": accepted_at,
                },
            )
            await session.execute(
                text(
                    "UPDATE tasks SET status = 'PENDING', progress = 0, "
                    "current_attempt = :attempt, last_error = NULL, updated_at = :accepted_at "
                    "WHERE task_id = :task_id"
                ),
                {
                    "task_id": task_id,
                    "attempt": next_attempt,
                    "accepted_at": accepted_at,
                },
            )
            event_row = (
                (
                    await session.execute(
                        text(
                            "INSERT INTO events "
                            "(task_id, attempt, step_id, correlation_id, agent, status, "
                            "progress, message, elapsed_ms, occurred_at) "
                            "VALUES (:task_id, :attempt, :step_id, :correlation_id, 'master', "
                            "'PENDING', 0, :message, 0, :accepted_at) RETURNING *"
                        ),
                        {
                            "task_id": task_id,
                            "attempt": next_attempt,
                            "step_id": str(failed_step_id),
                            "correlation_id": task["correlation_id"],
                            "message": "已接受失败任务重试",
                            "accepted_at": accepted_at,
                        },
                    )
                )
                .mappings()
                .one()
            )
            event = _event_from_row(event_row, ())
            return RetryAttemptResult(
                response=RetryAcceptedResponse(
                    task_id=task_id,
                    attempt=next_attempt,
                    status=TaskStatus.PENDING,
                    accepted_at=accepted_at,
                ),
                event=event,
                created=True,
            )

    async def save_plan(
        self,
        plan: ExecutionPlan,
        *,
        attempt: int,
        failed_model_call: ModelCallRecord | None = None,
        reused: bool = False,
    ) -> None:
        if reused and failed_model_call is not None:
            raise ValueError("reused plan cannot record a new failed model call")
        if failed_model_call is not None and (
            plan.source is not PlanSource.BUILTIN_RECOVERY
            or failed_model_call.status is not ModelCallStatus.FAILED
        ):
            raise ValueError(
                "failed_model_call requires a BUILTIN_RECOVERY plan and FAILED evidence"
            )
        model_call = None if reused else failed_model_call or plan.model_call
        payload = plan.model_dump(mode="json")
        async with self._sessions.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO plans (plan_id, task_id, attempt, source, payload, created_at) "
                    "VALUES (:plan_id, :task_id, :attempt, :source, CAST(:payload AS jsonb), "
                    ":created_at)"
                ),
                {
                    "plan_id": plan.plan_id,
                    "task_id": plan.task_id,
                    "attempt": attempt,
                    "source": plan.source.value,
                    "payload": _json(payload),
                    "created_at": plan.created_at,
                },
            )
            if model_call is not None:
                await session.execute(
                    text(
                        "INSERT INTO model_calls "
                        "(model_call_id, plan_id, model, started_at, duration_ms, status, "
                        "input_tokens, output_tokens, response_sha256, error_code) "
                        "VALUES (:model_call_id, :plan_id, :model, :started_at, :duration_ms, "
                        ":status, :input_tokens, :output_tokens, :response_sha256, :error_code)"
                    ),
                    {
                        "model_call_id": uuid4(),
                        "plan_id": plan.plan_id,
                        "model": model_call.model,
                        "started_at": model_call.started_at,
                        "duration_ms": model_call.duration_ms,
                        "status": model_call.status.value,
                        "input_tokens": model_call.input_tokens,
                        "output_tokens": model_call.output_tokens,
                        "response_sha256": model_call.response_sha256,
                        "error_code": model_call.error_code,
                    },
                )
            for step in plan.steps:
                await session.execute(
                    text(
                        "INSERT INTO steps "
                        "(task_id, attempt, step_id, plan_id, kind, agent, position, title, "
                        "depends_on_step_id, status, progress) "
                        "VALUES (:task_id, :attempt, :step_id, :plan_id, :kind, :agent, "
                        ":position, :title, :depends_on_step_id, 'PENDING', 0)"
                    ),
                    {
                        "task_id": plan.task_id,
                        "attempt": attempt,
                        "step_id": step.step_id,
                        "plan_id": plan.plan_id,
                        "kind": step.kind.value,
                        "agent": step.agent.value,
                        "position": step.order,
                        "title": step.title,
                        "depends_on_step_id": step.depends_on[0] if step.depends_on else None,
                    },
                )

    async def get_recovery_snapshot(
        self,
        task_id: UUID,
        attempt: int,
    ) -> RecoverySnapshot | None:
        """Load fail-closed retry evidence without mutating prior attempts."""

        if attempt <= 1:
            return None
        async with self._sessions() as session:
            retry = (
                (
                    await session.execute(
                        text(
                            "SELECT a.resume_from_step_id FROM attempts a "
                            "JOIN tasks t ON t.task_id = a.task_id "
                            "AND t.current_attempt = a.attempt "
                            "WHERE a.task_id = :task_id AND a.attempt = :attempt"
                        ),
                        {"task_id": task_id, "attempt": attempt},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if retry is None:
                raise RepositoryConflict("retry attempt is not current")
            resume_from_step_id = retry["resume_from_step_id"]
            if resume_from_step_id is None:
                return None

            source_attempt = attempt - 1
            plan_payload = await session.scalar(
                text("SELECT payload FROM plans WHERE task_id = :task_id AND attempt = :attempt"),
                {"task_id": task_id, "attempt": source_attempt},
            )
            plan = _validated_recovery_plan(plan_payload, task_id)
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT step_id, status, output FROM steps "
                            "WHERE task_id = :task_id AND attempt = :attempt"
                        ),
                        {"task_id": task_id, "attempt": source_attempt},
                    )
                )
                .mappings()
                .all()
            )
            outputs = {str(row["step_id"]): row for row in rows}
            return RecoverySnapshot(
                source_attempt=source_attempt,
                resume_from_step_id=str(resume_from_step_id),
                plan=plan,
                data=_validated_step_output(
                    outputs.get("prepare_data"),
                    DataPrepareResult,
                    task_id=task_id,
                    attempt=source_attempt,
                    step_id="prepare_data",
                ),
                analysis=_validated_step_output(
                    outputs.get("analyze_ndvi_change"),
                    AnalysisRunResult,
                    task_id=task_id,
                    attempt=source_attempt,
                    step_id="analyze_ndvi_change",
                ),
                quality=_validated_step_output(
                    outputs.get("evaluate_quality"),
                    QualityEvaluateResult,
                    task_id=task_id,
                    attempt=source_attempt,
                    step_id="evaluate_quality",
                ),
            )

    async def record_artifact(self, value: ArtifactCreate) -> None:
        await self.record_artifacts((value,))

    async def record_artifacts(self, values: tuple[ArtifactCreate, ...]) -> None:
        """Persist one complete Agent artifact set in a single transaction."""

        if not values:
            return
        scopes = {(value.artifact.task_id, value.artifact.attempt) for value in values}
        artifact_ids = {value.artifact.artifact_id for value in values}
        artifact_types = {value.artifact.artifact_type for value in values}
        if len(scopes) != 1:
            raise ValueError("artifact batch must belong to one task attempt")
        if len(artifact_ids) != len(values) or len(artifact_types) != len(values):
            raise ValueError("artifact batch requires unique identities and types")

        async with self._sessions.begin() as session:
            for value in values:
                artifact = value.artifact
                await session.execute(
                    text(
                        "INSERT INTO artifacts "
                        "(artifact_id, task_id, attempt, step_id, artifact_type, status, "
                        "media_type, storage_key, checksum_sha256, byte_size, created_at) "
                        "VALUES (:artifact_id, :task_id, :attempt, :step_id, :artifact_type, "
                        ":status, :media_type, :storage_key, :checksum_sha256, :byte_size, "
                        ":created_at)"
                    ),
                    {
                        "artifact_id": artifact.artifact_id,
                        "task_id": artifact.task_id,
                        "attempt": artifact.attempt,
                        "step_id": value.step_id,
                        "artifact_type": artifact.artifact_type.value,
                        "status": artifact.status.value,
                        "media_type": artifact.media_type,
                        "storage_key": value.storage_key,
                        "checksum_sha256": artifact.checksum_sha256,
                        "byte_size": artifact.byte_size,
                        "created_at": artifact.created_at,
                    },
                )

    async def transition_task(self, value: TransitionCreate) -> TaskEvent:
        return await self._record_task_event(value, require_state_change=True)

    async def record_progress(self, value: ProgressCreate) -> TaskEvent:
        """Atomically update progress and append an event without changing task state."""

        return await self._record_task_event(value, require_state_change=False)

    async def _record_task_event(
        self,
        value: TransitionCreate,
        *,
        require_state_change: bool,
    ) -> TaskEvent:
        async with self._sessions.begin() as session:
            task = (
                (
                    await session.execute(
                        text(
                            "SELECT status, progress, current_attempt, correlation_id "
                            "FROM tasks WHERE task_id = :task_id FOR UPDATE"
                        ),
                        {"task_id": value.task_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if task is None:
                raise RepositoryNotFound("task does not exist")
            if int(task["current_attempt"]) != value.attempt:
                raise RepositoryConflict("transition attempt is not current")

            current_status = TaskStatus(str(task["status"]))
            if require_state_change:
                require_task_transition(current_status, value.target_status)
            elif current_status is not value.target_status:
                raise RepositoryConflict("task status changed before progress was recorded")
            if value.progress < int(task["progress"]):
                raise RepositoryConflict("task progress cannot decrease")

            error_json = _json(value.error.model_dump(mode="json")) if value.error else None
            await session.execute(
                text(
                    "UPDATE tasks SET status = :status, progress = :progress, "
                    "last_error = CAST(:error AS jsonb), updated_at = :occurred_at "
                    "WHERE task_id = :task_id"
                ),
                {
                    "status": value.target_status.value,
                    "progress": value.progress,
                    "error": error_json,
                    "occurred_at": value.occurred_at,
                    "task_id": value.task_id,
                },
            )
            await session.execute(
                text(
                    "UPDATE attempts SET status = :status, "
                    "started_at = COALESCE(started_at, :occurred_at), "
                    "completed_at = CASE WHEN :terminal THEN :occurred_at ELSE completed_at END "
                    "WHERE task_id = :task_id AND attempt = :attempt"
                ),
                {
                    "status": value.target_status.value,
                    "occurred_at": value.occurred_at,
                    "terminal": value.target_status in {TaskStatus.COMPLETED, TaskStatus.FAILED},
                    "task_id": value.task_id,
                    "attempt": value.attempt,
                },
            )
            if value.step_status is not None:
                updated_step = await session.scalar(
                    text(
                        "UPDATE steps SET status = :status, progress = :progress, "
                        "started_at = COALESCE(:started_at, started_at), "
                        "completed_at = COALESCE(:completed_at, completed_at), "
                        "elapsed_ms = :elapsed_ms, error = CAST(:error AS jsonb), "
                        "output = COALESCE(CAST(:step_output AS jsonb), output) "
                        "WHERE task_id = :task_id AND attempt = :attempt AND step_id = :step_id "
                        "RETURNING step_id"
                    ),
                    {
                        "status": value.step_status.value,
                        "progress": value.step_progress,
                        "started_at": value.step_started_at,
                        "completed_at": value.step_completed_at,
                        "elapsed_ms": value.elapsed_ms,
                        "error": error_json,
                        "step_output": (
                            _json(value.step_output.model_dump(mode="json"))
                            if value.step_output is not None
                            else None
                        ),
                        "task_id": value.task_id,
                        "attempt": value.attempt,
                        "step_id": value.step_id,
                    },
                )
                if updated_step is None:
                    raise RepositoryNotFound("transition step does not exist")

            artifacts = await self._load_artifacts(
                session,
                value.artifact_ids,
                task_id=value.task_id,
                attempt=value.attempt,
            )
            sequence = await session.scalar(
                text(
                    "INSERT INTO events "
                    "(task_id, attempt, step_id, correlation_id, agent, status, progress, "
                    "message, elapsed_ms, occurred_at, error) "
                    "VALUES (:task_id, :attempt, :step_id, :correlation_id, :agent, :status, "
                    ":progress, :message, :elapsed_ms, :occurred_at, CAST(:error AS jsonb)) "
                    "RETURNING sequence"
                ),
                {
                    "task_id": value.task_id,
                    "attempt": value.attempt,
                    "step_id": value.step_id,
                    "correlation_id": task["correlation_id"],
                    "agent": value.agent.value,
                    "status": value.target_status.value,
                    "progress": value.progress,
                    "message": value.message,
                    "elapsed_ms": value.elapsed_ms,
                    "occurred_at": value.occurred_at,
                    "error": error_json,
                },
            )
            if sequence is None:
                raise RuntimeError("event insert did not return a sequence")
            for artifact in artifacts:
                await session.execute(
                    text(
                        "INSERT INTO event_artifacts "
                        "(sequence, artifact_id, task_id, attempt) "
                        "VALUES (:sequence, :artifact_id, :task_id, :attempt)"
                    ),
                    {
                        "sequence": int(sequence),
                        "artifact_id": artifact.artifact_id,
                        "task_id": value.task_id,
                        "attempt": value.attempt,
                    },
                )

            return TaskEvent(
                sequence=int(sequence),
                task_id=value.task_id,
                step_id=value.step_id,
                attempt=value.attempt,
                correlation_id=UUID(str(task["correlation_id"])),
                agent=value.agent,
                status=value.target_status,
                progress=value.progress,
                message=value.message,
                elapsed_ms=value.elapsed_ms,
                occurred_at=value.occurred_at,
                error=value.error,
                artifacts=artifacts,
            )

    async def claim_next_task(self, value: WorkerClaimRequest) -> WorkerClaim | None:
        """Atomically lease the oldest runnable task, reclaiming only expired leases."""
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "WITH candidate AS ("
                            "SELECT t.task_id, t.current_attempt FROM tasks t "
                            "LEFT JOIN worker_claims wc "
                            "ON wc.task_id = t.task_id AND wc.attempt = t.current_attempt "
                            "WHERE t.status NOT IN ('COMPLETED', 'FAILED') "
                            "AND (wc.task_id IS NULL OR wc.released_at IS NOT NULL "
                            "OR wc.lease_expires_at <= :claimed_at) "
                            "ORDER BY t.created_at, t.task_id "
                            "FOR UPDATE OF t SKIP LOCKED LIMIT 1"
                            ") "
                            "INSERT INTO worker_claims "
                            "(task_id, attempt, worker_id, claimed_at, heartbeat_at, "
                            "lease_expires_at, released_at) "
                            "SELECT task_id, current_attempt, :worker_id, :claimed_at, "
                            ":claimed_at, :claimed_at + :lease_seconds * INTERVAL '1 second', "
                            "NULL FROM candidate "
                            "ON CONFLICT (task_id, attempt) DO UPDATE SET "
                            "worker_id = EXCLUDED.worker_id, claimed_at = EXCLUDED.claimed_at, "
                            "heartbeat_at = EXCLUDED.heartbeat_at, "
                            "lease_expires_at = EXCLUDED.lease_expires_at, released_at = NULL "
                            "WHERE worker_claims.released_at IS NOT NULL "
                            "OR worker_claims.lease_expires_at <= EXCLUDED.claimed_at "
                            "RETURNING *"
                        ),
                        {
                            "worker_id": value.worker_id,
                            "claimed_at": value.claimed_at,
                            "lease_seconds": value.lease_seconds,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _worker_claim_from_row(row) if row is not None else None

    async def recover_interrupted_attempt(
        self,
        claim: WorkerClaim,
        *,
        recovered_at: UtcDateTime,
    ) -> InterruptedRecoveryResult | None:
        """Atomically fail a lease-reclaimed active attempt and queue its successor."""

        recovered_at = _UTC_DATETIME.validate_python(recovered_at)
        async with self._sessions.begin() as session:
            owned_claim = await session.scalar(
                text(
                    "SELECT 1 FROM worker_claims WHERE task_id = :task_id "
                    "AND attempt = :attempt AND worker_id = :worker_id "
                    "AND claimed_at = :claimed_at AND released_at IS NULL "
                    "AND lease_expires_at > :recovered_at FOR UPDATE"
                ),
                {
                    "task_id": claim.task_id,
                    "attempt": claim.attempt,
                    "worker_id": claim.worker_id,
                    "claimed_at": claim.claimed_at,
                    "recovered_at": recovered_at,
                },
            )
            if owned_claim is None:
                raise RepositoryConflict("worker claim is stale, released, or expired")

            task = (
                (
                    await session.execute(
                        text(
                            "SELECT status, progress, current_attempt, correlation_id "
                            "FROM tasks WHERE task_id = :task_id FOR UPDATE"
                        ),
                        {"task_id": claim.task_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if task is None:
                raise RepositoryNotFound("task does not exist")
            if int(task["current_attempt"]) != claim.attempt:
                raise RepositoryConflict("worker claim attempt is no longer current")

            status = TaskStatus(str(task["status"]))
            if status is TaskStatus.PENDING:
                return None
            try:
                resume_from_step_id = _INTERRUPTED_STEP_BY_STATUS[status]
            except KeyError as error:
                raise RepositoryConflict("terminal task cannot be recovered") from error

            interruption = StructuredError(
                code=ErrorCode.INTERNAL_ERROR,
                message="Master 进程中断，当前尝试已安全终止",
                retryable=True,
            )
            error_json = _json(interruption.model_dump(mode="json"))
            await session.execute(
                text(
                    "UPDATE steps SET status = 'FAILED', completed_at = :recovered_at, "
                    "error = CAST(:error AS jsonb) WHERE task_id = :task_id "
                    "AND attempt = :attempt AND step_id = :step_id "
                    "AND status IN ('PENDING', 'RUNNING')"
                ),
                {
                    "recovered_at": recovered_at,
                    "error": error_json,
                    "task_id": claim.task_id,
                    "attempt": claim.attempt,
                    "step_id": resume_from_step_id,
                },
            )
            await session.execute(
                text(
                    "UPDATE attempts SET status = 'FAILED', completed_at = :recovered_at "
                    "WHERE task_id = :task_id AND attempt = :attempt"
                ),
                {
                    "recovered_at": recovered_at,
                    "task_id": claim.task_id,
                    "attempt": claim.attempt,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO events "
                    "(task_id, attempt, step_id, correlation_id, agent, status, progress, "
                    "message, elapsed_ms, occurred_at, error) "
                    "VALUES (:task_id, :attempt, :step_id, :correlation_id, 'master', "
                    "'FAILED', :progress, :message, 0, :recovered_at, CAST(:error AS jsonb))"
                ),
                {
                    "task_id": claim.task_id,
                    "attempt": claim.attempt,
                    "step_id": resume_from_step_id,
                    "correlation_id": task["correlation_id"],
                    "progress": int(task["progress"]),
                    "message": interruption.message,
                    "recovered_at": recovered_at,
                    "error": error_json,
                },
            )

            retry_attempt = claim.attempt + 1
            await session.execute(
                text(
                    "INSERT INTO attempts "
                    "(task_id, attempt, status, resume_from_step_id, created_at) "
                    "VALUES (:task_id, :attempt, 'PENDING', :step_id, :recovered_at)"
                ),
                {
                    "task_id": claim.task_id,
                    "attempt": retry_attempt,
                    "step_id": resume_from_step_id,
                    "recovered_at": recovered_at,
                },
            )
            await session.execute(
                text(
                    "UPDATE tasks SET status = 'PENDING', progress = 0, "
                    "current_attempt = :attempt, last_error = NULL, updated_at = :recovered_at "
                    "WHERE task_id = :task_id"
                ),
                {
                    "attempt": retry_attempt,
                    "recovered_at": recovered_at,
                    "task_id": claim.task_id,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO events "
                    "(task_id, attempt, step_id, correlation_id, agent, status, progress, "
                    "message, elapsed_ms, occurred_at) "
                    "VALUES (:task_id, :attempt, :step_id, :correlation_id, 'master', "
                    "'PENDING', 0, :message, 0, :recovered_at)"
                ),
                {
                    "task_id": claim.task_id,
                    "attempt": retry_attempt,
                    "step_id": resume_from_step_id,
                    "correlation_id": task["correlation_id"],
                    "message": "已从 Master 中断点创建恢复尝试",
                    "recovered_at": recovered_at,
                },
            )
            return InterruptedRecoveryResult(
                task_id=claim.task_id,
                interrupted_attempt=claim.attempt,
                retry_attempt=retry_attempt,
                resume_from_step_id=resume_from_step_id,
            )

    async def renew_claim(
        self,
        claim: WorkerClaim,
        value: WorkerLeaseRenewal,
    ) -> WorkerClaim:
        """Extend an unexpired lease only when its original token still owns the task."""
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "UPDATE worker_claims SET heartbeat_at = :heartbeat_at, "
                            "lease_expires_at = "
                            ":heartbeat_at + :lease_seconds * INTERVAL '1 second' "
                            "WHERE task_id = :task_id AND attempt = :attempt "
                            "AND worker_id = :worker_id AND claimed_at = :claimed_at "
                            "AND released_at IS NULL AND heartbeat_at <= :heartbeat_at "
                            "AND lease_expires_at > :heartbeat_at RETURNING *"
                        ),
                        {
                            "heartbeat_at": value.heartbeat_at,
                            "lease_seconds": value.lease_seconds,
                            "task_id": claim.task_id,
                            "attempt": claim.attempt,
                            "worker_id": claim.worker_id,
                            "claimed_at": claim.claimed_at,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RepositoryConflict("worker claim is stale, released, or expired")
            return _worker_claim_from_row(row)

    async def release_claim(
        self,
        claim: WorkerClaim,
        *,
        released_at: UtcDateTime,
    ) -> WorkerClaim:
        """Release exactly the supplied lease token without touching a replacement lease."""
        released_at = _UTC_DATETIME.validate_python(released_at)
        async with self._sessions.begin() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "UPDATE worker_claims SET released_at = :released_at "
                            "WHERE task_id = :task_id AND attempt = :attempt "
                            "AND worker_id = :worker_id AND claimed_at = :claimed_at "
                            "AND released_at IS NULL RETURNING *"
                        ),
                        {
                            "released_at": released_at,
                            "task_id": claim.task_id,
                            "attempt": claim.attempt,
                            "worker_id": claim.worker_id,
                            "claimed_at": claim.claimed_at,
                        },
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RepositoryConflict("worker claim is stale or already released")
            return _worker_claim_from_row(row)

    async def get_task(self, task_id: UUID) -> TaskResponse | None:
        async with self._sessions() as session:
            task = (
                (
                    await session.execute(
                        text("SELECT * FROM tasks WHERE task_id = :task_id"),
                        {"task_id": task_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if task is None:
                return None
            attempt = int(task["current_attempt"])
            plan_payload = await session.scalar(
                text("SELECT payload FROM plans WHERE task_id = :task_id AND attempt = :attempt"),
                {"task_id": task_id, "attempt": attempt},
            )
            plan = ExecutionPlan.model_validate(plan_payload) if plan_payload is not None else None
            artifacts = await self._load_task_artifacts(session, task_id, attempt)
            artifacts_by_step: dict[str, list[ArtifactRef]] = {}
            for step_id, artifact in artifacts:
                artifacts_by_step.setdefault(step_id, []).append(artifact)
            step_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM steps WHERE task_id = :task_id AND attempt = :attempt "
                            "ORDER BY position"
                        ),
                        {"task_id": task_id, "attempt": attempt},
                    )
                )
                .mappings()
                .all()
            )
            steps = tuple(
                _step_from_row(row, tuple(artifacts_by_step.get(str(row["step_id"]), ())))
                for row in step_rows
            )
            publication = _validated_step_output(
                next(
                    (row for row in step_rows if str(row["step_id"]) == "publish_results"),
                    None,
                ),
                PublisherPublishResult,
                task_id=task_id,
                attempt=attempt,
                step_id="publish_results",
                correlation_id=UUID(str(task["correlation_id"])),
            )
            all_artifacts = tuple(artifact for _, artifact in artifacts)
            last_error = (
                StructuredError.model_validate(task["last_error"])
                if task["last_error"] is not None
                else None
            )
            return TaskResponse(
                task_id=UUID(str(task["task_id"])),
                query=str(task["query"]),
                status=TaskStatus(str(task["status"])),
                progress=int(task["progress"]),
                current_attempt=attempt,
                correlation_id=UUID(str(task["correlation_id"])),
                created_at=task["created_at"],
                updated_at=task["updated_at"],
                plan=plan,
                steps=steps,
                artifacts=all_artifacts,
                last_error=last_error,
                publication=publication,
            )

    async def list_events(
        self,
        task_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> tuple[TaskEvent, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM events WHERE task_id = :task_id "
                            "AND sequence > :after_sequence ORDER BY sequence LIMIT :limit"
                        ),
                        {
                            "task_id": task_id,
                            "after_sequence": after_sequence,
                            "limit": limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
            artifacts_by_sequence = await self._load_events_artifacts(
                session,
                task_id=task_id,
                after_sequence=after_sequence,
                limit=limit,
            )
            return tuple(
                _event_from_row(
                    row,
                    artifacts_by_sequence.get(int(row["sequence"]), ()),
                )
                for row in rows
            )

    async def get_watershed_geometry(self, watershed_id: UUID) -> dict[str, Any]:
        async with self._sessions() as session:
            geometry = await session.scalar(
                text(
                    "SELECT ST_AsGeoJSON(geometry)::jsonb FROM watersheds "
                    "WHERE watershed_id = :watershed_id"
                ),
                {"watershed_id": watershed_id},
            )
        if geometry is None:
            raise RepositoryNotFound("watershed does not exist")
        return dict(geometry)

    async def _load_artifacts(
        self,
        session: AsyncSession,
        artifact_ids: tuple[UUID, ...],
        *,
        task_id: UUID,
        attempt: int,
    ) -> tuple[ArtifactRef, ...]:
        artifacts: list[ArtifactRef] = []
        for artifact_id in artifact_ids:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM artifacts WHERE artifact_id = :artifact_id "
                            "AND task_id = :task_id AND attempt = :attempt"
                        ),
                        {"artifact_id": artifact_id, "task_id": task_id, "attempt": attempt},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise RepositoryNotFound("event artifact does not exist in task attempt")
            artifacts.append(_artifact_from_row(row))
        return tuple(artifacts)

    async def _load_task_artifacts(
        self,
        session: AsyncSession,
        task_id: UUID,
        attempt: int,
    ) -> tuple[tuple[str, ArtifactRef], ...]:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM artifacts WHERE task_id = :task_id AND attempt = :attempt "
                        "ORDER BY created_at, artifact_id"
                    ),
                    {"task_id": task_id, "attempt": attempt},
                )
            )
            .mappings()
            .all()
        )
        return tuple((str(row["step_id"]), _artifact_from_row(row)) for row in rows)

    async def _load_events_artifacts(
        self,
        session: AsyncSession,
        *,
        task_id: UUID,
        after_sequence: int,
        limit: int,
    ) -> dict[int, tuple[ArtifactRef, ...]]:
        rows = (
            (
                await session.execute(
                    text(
                        "WITH event_page AS ("
                        "SELECT sequence FROM events WHERE task_id = :task_id "
                        "AND sequence > :after_sequence ORDER BY sequence LIMIT :limit"
                        ") "
                        "SELECT ea.sequence, a.* FROM event_page ep "
                        "JOIN event_artifacts ea ON ea.sequence = ep.sequence "
                        "JOIN artifacts a ON a.artifact_id = ea.artifact_id "
                        "ORDER BY ea.sequence, a.created_at, a.artifact_id"
                    ),
                    {
                        "task_id": task_id,
                        "after_sequence": after_sequence,
                        "limit": limit,
                    },
                )
            )
            .mappings()
            .all()
        )
        artifacts: dict[int, list[ArtifactRef]] = {}
        for row in rows:
            artifacts.setdefault(int(row["sequence"]), []).append(_artifact_from_row(row))
        return {sequence: tuple(values) for sequence, values in artifacts.items()}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _validated_recovery_plan(payload: object, task_id: UUID) -> ExecutionPlan | None:
    if payload is None:
        return None
    try:
        plan = ExecutionPlan.model_validate(payload)
    except (TypeError, ValueError):
        return None
    return plan if plan.task_id == task_id else None


@overload
def _validated_step_output(
    row: RowMapping | None,
    model: type[DataPrepareResult],
    *,
    task_id: UUID,
    attempt: int,
    step_id: str,
    correlation_id: UUID | None = None,
) -> DataPrepareResult | None: ...


@overload
def _validated_step_output(
    row: RowMapping | None,
    model: type[AnalysisRunResult],
    *,
    task_id: UUID,
    attempt: int,
    step_id: str,
    correlation_id: UUID | None = None,
) -> AnalysisRunResult | None: ...


@overload
def _validated_step_output(
    row: RowMapping | None,
    model: type[QualityEvaluateResult],
    *,
    task_id: UUID,
    attempt: int,
    step_id: str,
    correlation_id: UUID | None = None,
) -> QualityEvaluateResult | None: ...


@overload
def _validated_step_output(
    row: RowMapping | None,
    model: type[PublisherPublishResult],
    *,
    task_id: UUID,
    attempt: int,
    step_id: str,
    correlation_id: UUID | None = None,
) -> PublisherPublishResult | None: ...


def _validated_step_output(
    row: RowMapping | None,
    model: (
        type[DataPrepareResult]
        | type[AnalysisRunResult]
        | type[QualityEvaluateResult]
        | type[PublisherPublishResult]
    ),
    *,
    task_id: UUID,
    attempt: int,
    step_id: str,
    correlation_id: UUID | None = None,
) -> DataPrepareResult | AnalysisRunResult | QualityEvaluateResult | PublisherPublishResult | None:
    if (
        row is None
        or row["status"] not in {StepStatus.COMPLETED.value, StepStatus.SKIPPED.value}
        or row["output"] is None
    ):
        return None
    try:
        output = model.model_validate(row["output"])
    except (TypeError, ValueError):
        return None
    if (
        output.task_id != task_id
        or output.attempt != attempt
        or output.step_id != step_id
        or (correlation_id is not None and output.correlation_id != correlation_id)
    ):
        return None
    return output


_UTC_DATETIME = TypeAdapter(UtcDateTime)


def _worker_claim_from_row(row: RowMapping) -> WorkerClaim:
    return WorkerClaim(
        task_id=UUID(str(row["task_id"])),
        attempt=int(row["attempt"]),
        worker_id=str(row["worker_id"]),
        claimed_at=row["claimed_at"],
        heartbeat_at=row["heartbeat_at"],
        lease_expires_at=row["lease_expires_at"],
        released_at=row["released_at"],
    )


def _artifact_from_row(row: RowMapping) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=UUID(str(row["artifact_id"])),
        task_id=UUID(str(row["task_id"])),
        attempt=int(row["attempt"]),
        artifact_type=ArtifactType(str(row["artifact_type"])),
        status=ArtifactStatus(str(row["status"])),
        media_type=str(row["media_type"]),
        created_at=row["created_at"],
        checksum_sha256=row["checksum_sha256"],
        byte_size=row["byte_size"],
    )


def _step_from_row(
    row: RowMapping,
    artifacts: tuple[ArtifactRef, ...],
) -> TaskStep:
    error = StructuredError.model_validate(row["error"]) if row["error"] is not None else None
    return TaskStep(
        step_id=str(row["step_id"]),
        kind=PlanStepKind(str(row["kind"])),
        agent=AgentName(str(row["agent"])),
        attempt=int(row["attempt"]),
        status=StepStatus(str(row["status"])),
        progress=int(row["progress"]),
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        elapsed_ms=row["elapsed_ms"],
        error=error,
        artifacts=artifacts,
    )


def _event_from_row(
    row: RowMapping,
    artifacts: tuple[ArtifactRef, ...],
) -> TaskEvent:
    error = StructuredError.model_validate(row["error"]) if row["error"] is not None else None
    return TaskEvent(
        sequence=int(row["sequence"]),
        task_id=UUID(str(row["task_id"])),
        step_id=str(row["step_id"]),
        attempt=int(row["attempt"]),
        correlation_id=UUID(str(row["correlation_id"])),
        agent=AgentName(str(row["agent"])),
        status=TaskStatus(str(row["status"])),
        progress=int(row["progress"]),
        message=str(row["message"]),
        elapsed_ms=int(row["elapsed_ms"]),
        occurred_at=row["occurred_at"],
        error=error,
        artifacts=artifacts,
    )
