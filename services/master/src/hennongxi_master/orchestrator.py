"""Durable Master orchestration for the fixed network Agent chain."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid4, uuid5

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
from hennongxi_master.repository import (
    ArtifactCreate,
    ProgressCreate,
    RecoverySnapshot,
    TransitionCreate,
)
from hennongxi_master.study_area import (
    StudyAreaConclusion,
    StudyAreaEvidence,
    StudyAreaGrounder,
)

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

    async def get_recovery_snapshot(
        self,
        task_id: UUID,
        attempt: int,
    ) -> RecoverySnapshot | None: ...

    async def save_plan(
        self,
        plan: ExecutionPlan,
        *,
        attempt: int,
        failed_model_call: ModelCallRecord | None = None,
        reused: bool = False,
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


class StudyAreaGrounding(Protocol):
    async def verify_query(self, query: str) -> StudyAreaEvidence: ...


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
        study_area_grounder: StudyAreaGrounding | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._agents = agents
        self._planner = planner
        self._event_publisher = event_publisher
        self._now = now or _utc_now
        self._study_area_grounder = study_area_grounder or StudyAreaGrounder(
            None,
            now=self._now,
        )

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
        study_area = await self._study_area_grounder.verify_query(task.query)
        _LOGGER.info(
            "study_area_grounded",
            **identity,
            conclusion=study_area.conclusion.value,
            reason_code=study_area.reason_code.value,
            duration_ms=study_area.duration_ms,
            retryable=study_area.retryable,
        )
        if study_area.conclusion is StudyAreaConclusion.REJECTED:
            error = StructuredError(
                code=ErrorCode.VALIDATION_ERROR,
                message="目前仅支持神农溪流域生态变化监测",
                retryable=False,
            )
            await self._persist_transition(
                TransitionCreate(
                    task_id=task.task_id,
                    attempt=attempt,
                    step_id="planning",
                    agent=AgentName.MASTER,
                    target_status=TaskStatus.FAILED,
                    progress=0,
                    message=(
                        f"研究区校验已拒绝（{study_area.conclusion.value}/"
                        f"{study_area.reason_code.value}）：{error.message}"
                    ),
                    elapsed_ms=study_area.duration_ms,
                    occurred_at=study_area.checked_at,
                    error=error,
                )
            )
            return await self._failed_task(task, attempt, TaskStatus.PENDING, error)

        recovery = await self._repository.get_recovery_snapshot(task.task_id, attempt)
        if recovery is not None:
            _LOGGER.info(
                "retry_checkpoint_selected",
                **identity,
                source_attempt=recovery.source_attempt,
                resume_from_step_id=recovery.resume_from_step_id,
            )
        _LOGGER.info("orchestration_started", **identity)
        await self._transition(
            task,
            attempt=attempt,
            step_id="planning",
            agent=AgentName.MASTER,
            target_status=TaskStatus.PLANNING,
            progress=5,
            message=_planning_message(
                study_area,
                recovering=recovery is not None and recovery.plan is not None,
            ),
            elapsed_ms=study_area.duration_ms,
            occurred_at=study_area.checked_at,
        )

        reused_plan = recovery is not None and recovery.plan is not None
        if reused_plan:
            assert recovery is not None and recovery.plan is not None
            planning = PlanningOutcome(
                plan=recovery.plan.model_copy(
                    update={"plan_id": uuid4(), "created_at": self._now()}
                )
            )
            _LOGGER.info(
                "retry_checkpoint_reused",
                **identity,
                source_attempt=recovery.source_attempt,
                step_id="planning",
            )
        else:
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
            reused=reused_plan,
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
        data_reused = (
            recovery is not None
            and recovery.resume_from_step_id
            in {"analyze_ndvi_change", "evaluate_quality", "publish_results"}
            and recovery.data is not None
            and _same_data_assets(recovery.data, data)
        )
        if data_reused:
            assert recovery is not None
            _LOGGER.info(
                "retry_checkpoint_reused",
                **identity,
                source_attempt=recovery.source_attempt,
                step_id="prepare_data",
            )
        elif recovery is not None and recovery.resume_from_step_id != "prepare_data":
            _LOGGER.info(
                "retry_checkpoint_recompute",
                **identity,
                source_attempt=recovery.source_attempt,
                step_id="prepare_data",
                reason=(
                    "data_inputs_changed"
                    if recovery.data is not None and not _same_data_assets(recovery.data, data)
                    else "checkpoint_evidence_unavailable"
                ),
            )

        await self._transition(
            task,
            attempt=attempt,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.ANALYZING,
            progress=25,
            message=("批准数据复核通过，沿用安全检查点" if data_reused else "批准数据校验完成"),
            elapsed_ms=data_elapsed,
            step_status=StepStatus.SKIPPED if data_reused else StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=self._now(),
            step_output=data,
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
        analysis_reuse_from = (
            recovery.source_attempt
            if recovery is not None
            and recovery.resume_from_step_id in {"evaluate_quality", "publish_results"}
            and recovery.analysis is not None
            and recovery.data is not None
            and _same_data_assets(recovery.data, data)
            else None
        )
        analysis_command = AnalysisRunCommand(
            task_id=task.task_id,
            step_id="analyze_ndvi_change",
            attempt=attempt,
            correlation_id=task.correlation_id,
            inputs=data.assets,
            reuse_from_attempt=analysis_reuse_from,
        )
        analysis_reused = False
        try:
            analysis = await self._agents.run_analysis(
                analysis_command,
                idempotency_key=_idempotency_key(task.task_id, attempt, "analyze_ndvi_change"),
            )
            analysis_reused = analysis_reuse_from is not None
        except AgentCallError as error:
            if analysis_reuse_from is None:
                return await self._fail_agent(
                    task,
                    attempt=attempt,
                    current_status=TaskStatus.ANALYZING,
                    progress=30,
                    error=error,
                )
            _LOGGER.warning(
                "retry_checkpoint_recompute",
                **identity,
                source_attempt=analysis_reuse_from,
                step_id="analyze_ndvi_change",
                reason="artifact_promotion_failed",
                error_code=error.error.code.value,
            )
            try:
                analysis = await self._agents.run_analysis(
                    analysis_command.model_copy(update={"reuse_from_attempt": None}),
                    idempotency_key=_idempotency_key(task.task_id, attempt, "analyze_ndvi_change"),
                )
            except AgentCallError as recompute_error:
                return await self._fail_agent(
                    task,
                    attempt=attempt,
                    current_status=TaskStatus.ANALYZING,
                    progress=30,
                    error=recompute_error,
                )
        if analysis_reused:
            assert recovery is not None
            _LOGGER.info(
                "retry_checkpoint_reused",
                **identity,
                source_attempt=recovery.source_attempt,
                step_id="analyze_ndvi_change",
            )
        elif (
            recovery is not None
            and recovery.resume_from_step_id in {"evaluate_quality", "publish_results"}
            and analysis_reuse_from is None
        ):
            _LOGGER.info(
                "retry_checkpoint_recompute",
                **identity,
                source_attempt=recovery.source_attempt,
                step_id="analyze_ndvi_change",
                reason=(
                    "data_inputs_changed"
                    if recovery.data is not None and not _same_data_assets(recovery.data, data)
                    else "checkpoint_evidence_unavailable"
                ),
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
            message=(
                "NDVI 分析成果校验通过并提升到当前尝试"
                if analysis_reused
                else "NDVI 分析成果已原子发布"
            ),
            elapsed_ms=analysis_elapsed,
            step_status=StepStatus.SKIPPED if analysis_reused else StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=self._now(),
            artifact_ids=tuple(artifact.artifact_id for artifact in analysis.artifacts),
            step_output=analysis,
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
        quality_reuse_from = (
            recovery.source_attempt
            if recovery is not None
            and recovery.resume_from_step_id == "publish_results"
            and recovery.quality is not None
            and recovery.quality.metrics.conclusion is QualityConclusion.PASS
            and recovery.quality.metrics.passed
            and analysis_reused
            else None
        )
        quality_command = QualityEvaluateCommand(
            task_id=task.task_id,
            step_id="evaluate_quality",
            attempt=attempt,
            correlation_id=task.correlation_id,
            artifacts=analysis.artifacts,
            analysis_elapsed_ms=analysis.elapsed_ms,
            reuse_from_attempt=quality_reuse_from,
        )
        quality_reused = False
        try:
            quality = await self._agents.evaluate_quality(
                quality_command,
                idempotency_key=_idempotency_key(task.task_id, attempt, "evaluate_quality"),
            )
            quality_reused = quality_reuse_from is not None
        except AgentCallError as error:
            if quality_reuse_from is None:
                return await self._fail_agent(
                    task,
                    attempt=attempt,
                    current_status=TaskStatus.QUALITY_CHECKING,
                    progress=60,
                    error=error,
                )
            _LOGGER.warning(
                "retry_checkpoint_recompute",
                **identity,
                source_attempt=quality_reuse_from,
                step_id="evaluate_quality",
                reason="artifact_promotion_failed",
                error_code=error.error.code.value,
            )
            try:
                quality = await self._agents.evaluate_quality(
                    quality_command.model_copy(update={"reuse_from_attempt": None}),
                    idempotency_key=_idempotency_key(task.task_id, attempt, "evaluate_quality"),
                )
            except AgentCallError as recompute_error:
                return await self._fail_agent(
                    task,
                    attempt=attempt,
                    current_status=TaskStatus.QUALITY_CHECKING,
                    progress=60,
                    error=recompute_error,
                )
        if quality_reused:
            assert recovery is not None
            _LOGGER.info(
                "retry_checkpoint_reused",
                **identity,
                source_attempt=recovery.source_attempt,
                step_id="evaluate_quality",
            )
        elif (
            recovery is not None
            and recovery.resume_from_step_id == "publish_results"
            and quality_reuse_from is None
        ):
            _LOGGER.info(
                "retry_checkpoint_recompute",
                **identity,
                source_attempt=recovery.source_attempt,
                step_id="evaluate_quality",
                reason=(
                    "analysis_was_recomputed"
                    if not analysis_reused
                    else "checkpoint_evidence_unavailable"
                ),
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
                step_output=quality,
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
            message=("质量报告复核通过并提升到当前尝试" if quality_reused else "质量核验通过"),
            elapsed_ms=quality_elapsed,
            step_status=StepStatus.SKIPPED if quality_reused else StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=self._now(),
            artifact_ids=(quality.artifact.artifact_id,),
            step_output=quality,
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
            step_output=published,
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
        occurred_at: datetime | None = None,
        step_status: StepStatus | None = None,
        step_progress: int | None = None,
        step_started_at: datetime | None = None,
        step_completed_at: datetime | None = None,
        artifact_ids: tuple[UUID, ...] = (),
        step_output: DataPrepareResult
        | AnalysisRunResult
        | QualityEvaluateResult
        | PublisherPublishResult
        | None = None,
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
                occurred_at=occurred_at or self._now(),
                step_status=step_status,
                step_progress=step_progress,
                step_started_at=step_started_at,
                step_completed_at=step_completed_at,
                artifact_ids=artifact_ids,
                step_output=step_output,
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
        step_output: DataPrepareResult
        | AnalysisRunResult
        | QualityEvaluateResult
        | PublisherPublishResult
        | None = None,
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
                step_output=step_output,
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


def _planning_message(evidence: StudyAreaEvidence, *, recovering: bool) -> str:
    if evidence.conclusion is StudyAreaConclusion.VERIFIED:
        planning_action = "恢复已验证的执行计划" if recovering else "正在生成受约束的执行计划"
        return (
            f"在线位置校验通过（{evidence.conclusion.value}/"
            f"{evidence.reason_code.value}）；{planning_action}"
        )
    planning_action = "恢复已验证的执行计划" if recovering else "继续生成受约束的执行计划"
    return (
        f"在线位置校验已降级（{evidence.conclusion.value}/"
        f"{evidence.reason_code.value}）；使用 G2 本地权威数据{planning_action}"
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _same_data_assets(previous: DataPrepareResult, current: DataPrepareResult) -> bool:
    return {asset.dataset_id: asset for asset in previous.assets} == {
        asset.dataset_id: asset for asset in current.assets
    }
