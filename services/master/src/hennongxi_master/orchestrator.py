"""Durable Master orchestration for the fixed network Agent chain."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid5

import structlog
from hennongxi_contracts import (
    AgentName,
    AnalysisRunCommand,
    AnalysisRunResult,
    ArtifactRef,
    ArtifactType,
    DataPrepareCommand,
    DataPrepareResult,
    ErrorCode,
    ExecutionPlan,
    LogicalDatasetId,
    ModelCallRecord,
    PublisherPublishCommand,
    PublisherPublishResult,
    QualityConclusion,
    QualityEvaluateCommand,
    QualityEvaluateResult,
    StepStatus,
    StructuredError,
    TaskEvent,
    TaskResponse,
    TaskStatus,
)

from hennongxi_master.agent_client import AgentCallError
from hennongxi_master.repository import ArtifactCreate, ProgressCreate, TransitionCreate

_LOGGER = structlog.get_logger("hennongxi.master.orchestrator")
_IDEMPOTENCY_NAMESPACE = UUID("917e647d-2f28-4dcc-9d05-27003c72e9bb")
_REQUIRED_COMPLETION_ARTIFACTS = frozenset(
    {
        ArtifactType.NDVI_BEFORE,
        ArtifactType.NDVI_AFTER,
        ArtifactType.NDVI_DIFFERENCE,
        ArtifactType.CHANGE_CLASSIFICATION,
        ArtifactType.AREA_STATISTICS,
        ArtifactType.QUALITY_REPORT,
        ArtifactType.PDF_REPORT,
    }
)


@dataclass(frozen=True, slots=True)
class PlanningOutcome:
    plan: ExecutionPlan
    failed_model_call: ModelCallRecord | None = None


class OrchestrationRepository(Protocol):
    async def get_task(self, task_id: UUID) -> TaskResponse | None: ...

    async def save_plan(
        self,
        plan: ExecutionPlan,
        *,
        attempt: int,
        failed_model_call: ModelCallRecord | None = None,
    ) -> None: ...

    async def record_artifacts(self, values: tuple[ArtifactCreate, ...]) -> None: ...

    async def transition_task(self, value: TransitionCreate) -> TaskEvent: ...

    async def record_progress(self, value: ProgressCreate) -> TaskEvent: ...


class EventPublisher(Protocol):
    async def publish(self, event: TaskEvent) -> bool: ...


class AgentClient(Protocol):
    async def prepare_data(self, command: DataPrepareCommand) -> DataPrepareResult: ...

    async def run_analysis(
        self,
        command: AnalysisRunCommand,
        *,
        idempotency_key: UUID,
    ) -> AnalysisRunResult: ...

    async def evaluate_quality(
        self,
        command: QualityEvaluateCommand,
        *,
        idempotency_key: UUID,
    ) -> QualityEvaluateResult: ...

    async def publish_results(
        self,
        command: PublisherPublishCommand,
        *,
        idempotency_key: UUID,
    ) -> PublisherPublishResult: ...


class TaskPlanner(Protocol):
    async def create_plan(self, task: TaskResponse) -> PlanningOutcome: ...


class OrchestrationConflict(RuntimeError):
    """Raised when a worker cannot safely start the requested task attempt."""


class TaskOrchestrator:
    """Execute one claimed task attempt and persist every observable state edge."""

    def __init__(
        self,
        repository: OrchestrationRepository,
        agents: AgentClient,
        planner: TaskPlanner,
        event_publisher: EventPublisher | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._agents = agents
        self._planner = planner
        self._event_publisher = event_publisher
        self._now = now or _utc_now

    async def run(self, task_id: UUID, *, attempt: int) -> TaskResponse:
        task = await self._repository.get_task(task_id)
        if task is None:
            raise OrchestrationConflict("claimed task does not exist")
        if task.current_attempt != attempt or task.status is not TaskStatus.PENDING:
            raise OrchestrationConflict("claimed task attempt is not pending")

        identity = {
            "task_id": str(task.task_id),
            "attempt": attempt,
            "correlation_id": str(task.correlation_id),
        }
        _LOGGER.info("orchestration_started", **identity)
        await self._transition(
            task,
            attempt=attempt,
            step_id="planning",
            agent=AgentName.MASTER,
            target_status=TaskStatus.PLANNING,
            progress=5,
            message="正在生成受约束的执行计划",
        )

        try:
            planning = await self._planner.create_plan(task)
        except Exception as planner_error:
            _LOGGER.error(
                "planning_failed",
                **identity,
                error_type=type(planner_error).__name__,
            )
            return await self._fail_without_step(
                task,
                attempt=attempt,
                current_status=TaskStatus.PLANNING,
                progress=5,
                step_id="planning",
                agent=AgentName.MASTER,
                error=StructuredError(
                    code=ErrorCode.INTERNAL_ERROR,
                    message="执行计划生成失败",
                    retryable=True,
                ),
            )
        if planning.plan.task_id != task.task_id:
            return await self._fail_without_step(
                task,
                attempt=attempt,
                current_status=TaskStatus.PLANNING,
                progress=5,
                step_id="planning",
                agent=AgentName.MASTER,
                error=StructuredError(
                    code=ErrorCode.INVALID_PLAN,
                    message="执行计划与任务身份不一致",
                    retryable=False,
                ),
            )
        await self._repository.save_plan(
            planning.plan,
            attempt=attempt,
            failed_model_call=planning.failed_model_call,
        )

        await self._transition(
            task,
            attempt=attempt,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.DATA_PREPARING,
            progress=10,
            message="Data Agent 开始校验批准数据",
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=self._now(),
        )
        data_started = monotonic()
        try:
            data = await self._agents.prepare_data(
                DataPrepareCommand(
                    task_id=task.task_id,
                    step_id="prepare_data",
                    attempt=attempt,
                    correlation_id=task.correlation_id,
                    dataset_ids=tuple(LogicalDatasetId),
                )
            )
        except AgentCallError as error:
            return await self._fail_agent(
                task,
                attempt=attempt,
                current_status=TaskStatus.DATA_PREPARING,
                progress=10,
                error=error,
            )
        data_elapsed = _elapsed_ms(data_started)

        await self._transition(
            task,
            attempt=attempt,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.ANALYZING,
            progress=25,
            message="批准数据校验完成",
            elapsed_ms=data_elapsed,
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=self._now(),
        )
        await self._progress(
            task,
            attempt=attempt,
            step_id="analyze_ndvi_change",
            agent=AgentName.ANALYSIS,
            task_status=TaskStatus.ANALYZING,
            progress=30,
            message="Analysis Agent 开始计算 NDVI 变化",
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=self._now(),
        )
        analysis_started = monotonic()
        try:
            analysis = await self._agents.run_analysis(
                AnalysisRunCommand(
                    task_id=task.task_id,
                    step_id="analyze_ndvi_change",
                    attempt=attempt,
                    correlation_id=task.correlation_id,
                    inputs=data.assets,
                ),
                idempotency_key=_idempotency_key(task.task_id, attempt, "analyze_ndvi_change"),
            )
        except AgentCallError as error:
            return await self._fail_agent(
                task,
                attempt=attempt,
                current_status=TaskStatus.ANALYZING,
                progress=30,
                error=error,
            )
        analysis_elapsed = _elapsed_ms(analysis_started)
        await self._record_artifacts(
            analysis.artifacts,
            step_id="analyze_ndvi_change",
            agent=AgentName.ANALYSIS,
        )

        await self._transition(
            task,
            attempt=attempt,
            step_id="analyze_ndvi_change",
            agent=AgentName.ANALYSIS,
            target_status=TaskStatus.QUALITY_CHECKING,
            progress=55,
            message="NDVI 分析成果已原子发布",
            elapsed_ms=analysis_elapsed,
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=self._now(),
            artifact_ids=tuple(artifact.artifact_id for artifact in analysis.artifacts),
        )
        await self._progress(
            task,
            attempt=attempt,
            step_id="evaluate_quality",
            agent=AgentName.QUALITY,
            task_status=TaskStatus.QUALITY_CHECKING,
            progress=60,
            message="Quality Agent 开始独立核验成果",
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=self._now(),
        )
        quality_started = monotonic()
        try:
            quality = await self._agents.evaluate_quality(
                QualityEvaluateCommand(
                    task_id=task.task_id,
                    step_id="evaluate_quality",
                    attempt=attempt,
                    correlation_id=task.correlation_id,
                    artifacts=analysis.artifacts,
                    analysis_elapsed_ms=analysis.elapsed_ms,
                ),
                idempotency_key=_idempotency_key(task.task_id, attempt, "evaluate_quality"),
            )
        except AgentCallError as error:
            return await self._fail_agent(
                task,
                attempt=attempt,
                current_status=TaskStatus.QUALITY_CHECKING,
                progress=60,
                error=error,
            )
        quality_elapsed = _elapsed_ms(quality_started)
        await self._record_artifacts(
            (quality.artifact,),
            step_id="evaluate_quality",
            agent=AgentName.QUALITY,
        )

        if quality.metrics.conclusion is not QualityConclusion.PASS or not quality.metrics.passed:
            await self._progress(
                task,
                attempt=attempt,
                step_id="evaluate_quality",
                agent=AgentName.QUALITY,
                task_status=TaskStatus.QUALITY_CHECKING,
                progress=75,
                message="质量核验完成，但结论未通过",
                elapsed_ms=quality_elapsed,
                step_status=StepStatus.COMPLETED,
                step_progress=100,
                step_completed_at=self._now(),
                artifact_ids=(quality.artifact.artifact_id,),
            )
            return await self._fail_without_step(
                task,
                attempt=attempt,
                current_status=TaskStatus.QUALITY_CHECKING,
                progress=75,
                step_id="evaluate_quality",
                agent=AgentName.QUALITY,
                error=StructuredError(
                    code=ErrorCode.QUALITY_FAILED,
                    message="质量检查未通过，未发布不完整成果",
                    retryable=False,
                ),
            )

        await self._transition(
            task,
            attempt=attempt,
            step_id="evaluate_quality",
            agent=AgentName.QUALITY,
            target_status=TaskStatus.PUBLISHING,
            progress=75,
            message="质量核验通过",
            elapsed_ms=quality_elapsed,
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=self._now(),
            artifact_ids=(quality.artifact.artifact_id,),
        )
        await self._progress(
            task,
            attempt=attempt,
            step_id="publish_results",
            agent=AgentName.PUBLISHER,
            task_status=TaskStatus.PUBLISHING,
            progress=80,
            message="Publisher Agent 开始发布地图与中文报告",
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=self._now(),
        )
        publisher_started = monotonic()
        try:
            published = await self._agents.publish_results(
                PublisherPublishCommand(
                    task_id=task.task_id,
                    step_id="publish_results",
                    attempt=attempt,
                    correlation_id=task.correlation_id,
                    artifacts=(*analysis.artifacts, quality.artifact),
                    quality=quality.metrics,
                ),
                idempotency_key=_idempotency_key(task.task_id, attempt, "publish_results"),
            )
        except AgentCallError as error:
            return await self._fail_agent(
                task,
                attempt=attempt,
                current_status=TaskStatus.PUBLISHING,
                progress=80,
                error=error,
            )
        publisher_elapsed = _elapsed_ms(publisher_started)
        await self._record_artifacts(
            (published.report,),
            step_id="publish_results",
            agent=AgentName.PUBLISHER,
        )

        produced_types = {
            *(artifact.artifact_type for artifact in analysis.artifacts),
            quality.artifact.artifact_type,
            published.report.artifact_type,
        }
        if produced_types != _REQUIRED_COMPLETION_ARTIFACTS:
            return await self._fail_without_step(
                task,
                attempt=attempt,
                current_status=TaskStatus.PUBLISHING,
                progress=80,
                step_id="publish_results",
                agent=AgentName.PUBLISHER,
                error=StructuredError(
                    code=ErrorCode.PUBLISHING_FAILED,
                    message="发布成果集合不完整",
                    retryable=True,
                ),
            )

        await self._transition(
            task,
            attempt=attempt,
            step_id="publish_results",
            agent=AgentName.PUBLISHER,
            target_status=TaskStatus.COMPLETED,
            progress=100,
            message="地图与中文报告发布完成",
            elapsed_ms=publisher_elapsed,
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=self._now(),
            artifact_ids=(published.report.artifact_id,),
        )
        completed = await self._require_task(task.task_id)
        _LOGGER.info("orchestration_completed", **identity, artifact_count=len(completed.artifacts))
        return completed

    async def _record_artifacts(
        self,
        artifacts: tuple[ArtifactRef, ...],
        *,
        step_id: str,
        agent: AgentName,
    ) -> None:
        await self._repository.record_artifacts(
            tuple(
                ArtifactCreate(
                    artifact=artifact,
                    step_id=step_id,
                    storage_key=(
                        f"{artifact.task_id}/attempt-{artifact.attempt}/"
                        f"{agent.value}/{artifact.artifact_type.value.lower()}"
                    ),
                )
                for artifact in artifacts
            )
        )

    async def _transition(
        self,
        task: TaskResponse,
        *,
        attempt: int,
        step_id: str,
        agent: AgentName,
        target_status: TaskStatus,
        progress: int,
        message: str,
        elapsed_ms: int = 0,
        step_status: StepStatus | None = None,
        step_progress: int | None = None,
        step_started_at: datetime | None = None,
        step_completed_at: datetime | None = None,
        artifact_ids: tuple[UUID, ...] = (),
    ) -> None:
        await self._persist_transition(
            TransitionCreate(
                task_id=task.task_id,
                attempt=attempt,
                step_id=step_id,
                agent=agent,
                target_status=target_status,
                progress=progress,
                message=message,
                elapsed_ms=elapsed_ms,
                occurred_at=self._now(),
                step_status=step_status,
                step_progress=step_progress,
                step_started_at=step_started_at,
                step_completed_at=step_completed_at,
                artifact_ids=artifact_ids,
            )
        )

    async def _progress(
        self,
        task: TaskResponse,
        *,
        attempt: int,
        step_id: str,
        agent: AgentName,
        task_status: TaskStatus,
        progress: int,
        message: str,
        step_status: StepStatus,
        step_progress: int,
        elapsed_ms: int = 0,
        step_started_at: datetime | None = None,
        step_completed_at: datetime | None = None,
        artifact_ids: tuple[UUID, ...] = (),
    ) -> None:
        event = await self._repository.record_progress(
            ProgressCreate(
                task_id=task.task_id,
                attempt=attempt,
                step_id=step_id,
                agent=agent,
                target_status=task_status,
                progress=progress,
                message=message,
                elapsed_ms=elapsed_ms,
                occurred_at=self._now(),
                step_status=step_status,
                step_progress=step_progress,
                step_started_at=step_started_at,
                step_completed_at=step_completed_at,
                artifact_ids=artifact_ids,
            )
        )
        await self._publish_event(event)

    async def _fail_agent(
        self,
        task: TaskResponse,
        *,
        attempt: int,
        current_status: TaskStatus,
        progress: int,
        error: AgentCallError,
    ) -> TaskResponse:
        await self._persist_transition(
            TransitionCreate(
                task_id=task.task_id,
                attempt=attempt,
                step_id=error.step_id,
                agent=error.agent,
                target_status=TaskStatus.FAILED,
                progress=progress,
                message=f"{error.agent.value} Agent 执行失败",
                elapsed_ms=error.elapsed_ms,
                occurred_at=self._now(),
                error=error.error,
                step_status=StepStatus.FAILED,
                step_progress=0,
                step_completed_at=self._now(),
            )
        )
        return await self._failed_task(task, attempt, current_status, error.error)

    async def _fail_without_step(
        self,
        task: TaskResponse,
        *,
        attempt: int,
        current_status: TaskStatus,
        progress: int,
        step_id: str,
        agent: AgentName,
        error: StructuredError,
    ) -> TaskResponse:
        await self._persist_transition(
            TransitionCreate(
                task_id=task.task_id,
                attempt=attempt,
                step_id=step_id,
                agent=agent,
                target_status=TaskStatus.FAILED,
                progress=progress,
                message=error.message,
                elapsed_ms=0,
                occurred_at=self._now(),
                error=error,
            )
        )
        return await self._failed_task(task, attempt, current_status, error)

    async def _persist_transition(self, value: TransitionCreate) -> None:
        event = await self._repository.transition_task(value)
        await self._publish_event(event)

    async def _publish_event(self, event: TaskEvent) -> None:
        if self._event_publisher is None:
            return
        try:
            await self._event_publisher.publish(event)
        except Exception as error:
            # Redis is an acceleration layer; durable orchestration must continue.
            _LOGGER.warning(
                "task_event_publish_failed",
                task_id=str(event.task_id),
                sequence=event.sequence,
                correlation_id=str(event.correlation_id),
                error_type=type(error).__name__,
            )

    async def _failed_task(
        self,
        task: TaskResponse,
        attempt: int,
        current_status: TaskStatus,
        error: StructuredError,
    ) -> TaskResponse:
        _LOGGER.warning(
            "orchestration_failed",
            task_id=str(task.task_id),
            attempt=attempt,
            correlation_id=str(task.correlation_id),
            failed_from_status=current_status.value,
            error_code=error.code.value,
            retryable=error.retryable,
        )
        return await self._require_task(task.task_id)

    async def _require_task(self, task_id: UUID) -> TaskResponse:
        task = await self._repository.get_task(task_id)
        if task is None:
            raise RuntimeError("durable task disappeared during orchestration")
        return task


def _idempotency_key(task_id: UUID, attempt: int, step_id: str) -> UUID:
    return uuid5(_IDEMPOTENCY_NAMESPACE, f"{task_id}:{attempt}:{step_id}")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))
