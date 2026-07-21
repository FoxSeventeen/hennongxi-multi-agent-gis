from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4, uuid5

import pytest
from hennongxi_contracts import (
    AgentName,
    AnalysisRunCommand,
    AnalysisRunResult,
    AreaStatistics,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    DataAssetRef,
    DataPrepareCommand,
    DataPrepareResult,
    ErrorCode,
    LogicalDatasetId,
    PublishedResource,
    PublisherPublishCommand,
    PublisherPublishResult,
    QualityConclusion,
    QualityEvaluateCommand,
    QualityEvaluateResult,
    QualityMetrics,
    QualityThresholds,
    StepStatus,
    StructuredError,
    TaskEvent,
    TaskResponse,
    TaskStatus,
    TileArtifactType,
    TileLegendEntry,
    TileMetadata,
)
from hennongxi_master.agent_client import AgentCallError
from hennongxi_master.orchestrator import PlanningOutcome, TaskOrchestrator
from hennongxi_master.planning import build_builtin_recovery_plan
from hennongxi_master.repository import ArtifactCreate, ProgressCreate, TransitionCreate

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_TASK_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
SHA256 = "a" * 64


def _artifact(artifact_type: ArtifactType) -> ArtifactRef:
    media_type = {
        ArtifactType.AREA_STATISTICS: "application/json",
        ArtifactType.QUALITY_REPORT: "application/json",
        ArtifactType.PDF_REPORT: "application/pdf",
    }.get(artifact_type, "image/tiff; application=geotiff")
    return ArtifactRef(
        artifact_id=uuid5(TASK_ID, artifact_type.value),
        task_id=TASK_ID,
        attempt=1,
        artifact_type=artifact_type,
        status=ArtifactStatus.COMPLETE,
        media_type=media_type,
        created_at=NOW,
        checksum_sha256=SHA256,
        byte_size=10,
    )


def _analysis_artifacts() -> tuple[ArtifactRef, ...]:
    return tuple(
        _artifact(artifact_type)
        for artifact_type in (
            ArtifactType.NDVI_BEFORE,
            ArtifactType.NDVI_AFTER,
            ArtifactType.NDVI_DIFFERENCE,
            ArtifactType.CHANGE_CLASSIFICATION,
            ArtifactType.AREA_STATISTICS,
        )
    )


def _assets() -> tuple[DataAssetRef, ...]:
    return tuple(
        DataAssetRef(
            dataset_id=dataset_id,
            checksum_sha256=SHA256,
            byte_size=10,
        )
        for dataset_id in LogicalDatasetId
    )


def _quality(conclusion: QualityConclusion = QualityConclusion.PASS) -> QualityMetrics:
    return QualityMetrics(
        coverage_ratio=0.98,
        valid_pixel_ratio=0.96,
        output_complete=True,
        elapsed_ms=25,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.90,
        ),
        conclusion=conclusion,
        passed=conclusion is QualityConclusion.PASS,
        evidence=("范围通过", "像元通过", "成果完整", "耗时已记录"),
    )


def _tile_metadata(artifact_type: TileArtifactType) -> TileMetadata:
    start = date(2019, 8, 19)
    end = date(2024, 8, 12)
    return TileMetadata(
        artifact_type=artifact_type,
        bounds_wgs84=(110.0, 31.0, 111.0, 32.0),
        start_date=end if artifact_type is TileArtifactType.NDVI_AFTER else start,
        end_date=start if artifact_type is TileArtifactType.NDVI_BEFORE else end,
        units="NDVI",
        attribution="Copernicus Sentinel-2 / Element 84 Earth Search",
        legend=(
            TileLegendEntry(value=-1, label="低", color="#8C510A"),
            TileLegendEntry(value=0, label="中", color="#F6E8C3"),
            TileLegendEntry(value=1, label="高", color="#01665E"),
        ),
    )


def _publisher_result() -> PublisherPublishResult:
    report = _artifact(ArtifactType.PDF_REPORT)
    resources = tuple(
        PublishedResource(
            artifact_id=_artifact(ArtifactType(artifact_type.value)).artifact_id,
            tile_template=(f"/api/v1/tiles/{TASK_ID}/{artifact_type.value}/{{z}}/{{x}}/{{y}}.png"),
            tile_metadata=_tile_metadata(artifact_type),
        )
        for artifact_type in TileArtifactType
    )
    return PublisherPublishResult(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=CORRELATION_ID,
        resources=(
            *resources,
            PublishedResource(
                artifact_id=report.artifact_id,
                download_path=(f"/api/v1/tasks/{TASK_ID}/artifacts/{report.artifact_id}/download"),
            ),
        ),
        report=report,
    )


class _Repository:
    def __init__(self) -> None:
        self.task = TaskResponse(
            task_id=TASK_ID,
            query="分析神农溪植被变化",
            status=TaskStatus.PENDING,
            progress=0,
            current_attempt=1,
            correlation_id=CORRELATION_ID,
            created_at=NOW,
            updated_at=NOW,
        )
        self.records: list[TransitionCreate | ProgressCreate] = []
        self.artifacts: dict[UUID, ArtifactRef] = {}
        self.sequence = 0

    async def get_task(self, task_id: UUID) -> TaskResponse | None:
        return self.task if task_id == TASK_ID else None

    async def save_plan(self, plan: object, **_kwargs: object) -> None:
        self.task = self.task.model_copy(update={"plan": plan})

    async def record_artifacts(self, values: tuple[ArtifactCreate, ...]) -> None:
        for value in values:
            self.artifacts[value.artifact.artifact_id] = value.artifact
        self.task = self.task.model_copy(update={"artifacts": tuple(self.artifacts.values())})

    async def transition_task(self, value: TransitionCreate) -> TaskEvent:
        self.records.append(value)
        return self._apply(value)

    async def record_progress(self, value: ProgressCreate) -> TaskEvent:
        self.records.append(value)
        return self._apply(value)

    def _apply(self, value: TransitionCreate) -> TaskEvent:
        self.sequence += 1
        error = value.error if value.target_status is TaskStatus.FAILED else None
        self.task = self.task.model_copy(
            update={
                "status": value.target_status,
                "progress": value.progress,
                "updated_at": value.occurred_at,
                "last_error": error,
            }
        )
        return TaskEvent(
            sequence=self.sequence,
            task_id=value.task_id,
            step_id=value.step_id,
            attempt=value.attempt,
            correlation_id=CORRELATION_ID,
            agent=value.agent,
            status=value.target_status,
            progress=value.progress,
            message=value.message,
            elapsed_ms=value.elapsed_ms,
            occurred_at=value.occurred_at,
            error=value.error,
            artifacts=tuple(self.artifacts[value_id] for value_id in value.artifact_ids),
        )


class _Planner:
    def __init__(self, task_id: UUID = TASK_ID) -> None:
        self.task_id = task_id

    async def create_plan(self, task: TaskResponse) -> PlanningOutcome:
        return PlanningOutcome(
            plan=build_builtin_recovery_plan(
                task_id=self.task_id,
                plan_id=uuid4(),
                created_at=NOW,
            )
        )


class _EventPublisher:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.events: list[TaskEvent] = []

    async def publish(self, event: TaskEvent) -> bool:
        self.events.append(event)
        return self.available


class _FailingPlanner:
    async def create_plan(self, task: TaskResponse) -> PlanningOutcome:
        del task
        raise RuntimeError("private planner detail")


class _Agents:
    def __init__(
        self,
        *,
        fail_at: AgentName | None = None,
        quality_conclusion: QualityConclusion = QualityConclusion.PASS,
    ) -> None:
        self.fail_at = fail_at
        self.quality_conclusion = quality_conclusion
        self.commands: list[
            DataPrepareCommand
            | AnalysisRunCommand
            | QualityEvaluateCommand
            | PublisherPublishCommand
        ] = []

    def _accept(
        self,
        agent: AgentName,
        command: DataPrepareCommand
        | AnalysisRunCommand
        | QualityEvaluateCommand
        | PublisherPublishCommand,
    ) -> None:
        self.commands.append(command)
        if self.fail_at is agent:
            raise AgentCallError(
                agent=agent,
                step_id=command.step_id,
                error=StructuredError(
                    code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                    message=f"{agent.value} Agent 暂时不可用",
                    retryable=True,
                ),
                elapsed_ms=125,
            )

    async def prepare_data(self, command: DataPrepareCommand) -> DataPrepareResult:
        self._accept(AgentName.DATA, command)
        return DataPrepareResult(
            task_id=TASK_ID,
            step_id=command.step_id,
            attempt=1,
            correlation_id=CORRELATION_ID,
            assets=_assets(),
        )

    async def run_analysis(
        self,
        command: AnalysisRunCommand,
        *,
        idempotency_key: UUID,
    ) -> AnalysisRunResult:
        assert idempotency_key.version == 5
        self._accept(AgentName.ANALYSIS, command)
        return AnalysisRunResult(
            task_id=TASK_ID,
            step_id=command.step_id,
            attempt=1,
            correlation_id=CORRELATION_ID,
            artifacts=_analysis_artifacts(),
            statistics=AreaStatistics(
                increase_hectares=10,
                stable_hectares=20,
                decrease_hectares=5,
                valid_hectares=35,
            ),
            elapsed_ms=120,
        )

    async def evaluate_quality(
        self,
        command: QualityEvaluateCommand,
        *,
        idempotency_key: UUID,
    ) -> QualityEvaluateResult:
        assert idempotency_key.version == 5
        self._accept(AgentName.QUALITY, command)
        return QualityEvaluateResult(
            task_id=TASK_ID,
            step_id=command.step_id,
            attempt=1,
            correlation_id=CORRELATION_ID,
            metrics=_quality(self.quality_conclusion),
            artifact=_artifact(ArtifactType.QUALITY_REPORT),
        )

    async def publish_results(
        self,
        command: PublisherPublishCommand,
        *,
        idempotency_key: UUID,
    ) -> PublisherPublishResult:
        assert idempotency_key.version == 5
        self._accept(AgentName.PUBLISHER, command)
        return _publisher_result()


@pytest.mark.asyncio
async def test_orchestrator_completes_fixed_chain_with_one_durable_identity() -> None:
    repository = _Repository()
    agents = _Agents()
    publisher = _EventPublisher()

    result = await TaskOrchestrator(repository, agents, _Planner(), publisher).run(
        TASK_ID,
        attempt=1,
    )

    assert result.status is TaskStatus.COMPLETED
    assert result.progress == 100
    assert {artifact.artifact_type for artifact in result.artifacts} == {
        ArtifactType.NDVI_BEFORE,
        ArtifactType.NDVI_AFTER,
        ArtifactType.NDVI_DIFFERENCE,
        ArtifactType.CHANGE_CLASSIFICATION,
        ArtifactType.AREA_STATISTICS,
        ArtifactType.QUALITY_REPORT,
        ArtifactType.PDF_REPORT,
    }
    transitions = [
        record.target_status
        for record in repository.records
        if not isinstance(record, ProgressCreate)
    ]
    assert transitions == [
        TaskStatus.PLANNING,
        TaskStatus.DATA_PREPARING,
        TaskStatus.ANALYZING,
        TaskStatus.QUALITY_CHECKING,
        TaskStatus.PUBLISHING,
        TaskStatus.COMPLETED,
    ]
    assert [record.progress for record in repository.records] == sorted(
        record.progress for record in repository.records
    )
    assert [command.step_id for command in agents.commands] == [
        "prepare_data",
        "analyze_ndvi_change",
        "evaluate_quality",
        "publish_results",
    ]
    assert all(
        command.task_id == TASK_ID
        and command.attempt == 1
        and command.correlation_id == CORRELATION_ID
        for command in agents.commands
    )
    completed_steps = {
        record.step_id: record.step_output
        for record in repository.records
        if record.step_status is StepStatus.COMPLETED
    }
    assert isinstance(completed_steps["prepare_data"], DataPrepareResult)
    assert isinstance(completed_steps["analyze_ndvi_change"], AnalysisRunResult)
    assert isinstance(completed_steps["evaluate_quality"], QualityEvaluateResult)
    assert isinstance(completed_steps["publish_results"], PublisherPublishResult)
    assert [event.sequence for event in publisher.events] == list(
        range(1, len(repository.records) + 1)
    )


@pytest.mark.asyncio
async def test_redis_publish_failure_never_fails_durable_orchestration() -> None:
    repository = _Repository()
    publisher = _EventPublisher(available=False)

    result = await TaskOrchestrator(repository, _Agents(), _Planner(), publisher).run(
        TASK_ID,
        attempt=1,
    )

    assert result.status is TaskStatus.COMPLETED
    assert len(publisher.events) == len(repository.records)


@pytest.mark.asyncio
async def test_agent_failure_marks_current_step_failed_and_stops_downstream() -> None:
    repository = _Repository()
    agents = _Agents(fail_at=AgentName.ANALYSIS)

    result = await TaskOrchestrator(repository, agents, _Planner()).run(TASK_ID, attempt=1)

    assert result.status is TaskStatus.FAILED
    assert result.last_error is not None
    assert result.last_error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert [command.step_id for command in agents.commands] == [
        "prepare_data",
        "analyze_ndvi_change",
    ]
    failed = repository.records[-1]
    assert failed.target_status is TaskStatus.FAILED
    assert failed.step_id == "analyze_ndvi_change"
    assert failed.agent is AgentName.ANALYSIS
    assert failed.step_status is not None and failed.step_status.value == "FAILED"


@pytest.mark.asyncio
async def test_nonpassing_quality_is_persisted_and_never_published() -> None:
    repository = _Repository()
    agents = _Agents(quality_conclusion=QualityConclusion.FAIL)

    result = await TaskOrchestrator(repository, agents, _Planner()).run(TASK_ID, attempt=1)

    assert result.status is TaskStatus.FAILED
    assert result.last_error is not None
    assert result.last_error.code is ErrorCode.QUALITY_FAILED
    assert [command.step_id for command in agents.commands] == [
        "prepare_data",
        "analyze_ndvi_change",
        "evaluate_quality",
    ]
    assert ArtifactType.QUALITY_REPORT in {artifact.artifact_type for artifact in result.artifacts}
    quality_completion = repository.records[-2]
    assert isinstance(quality_completion, ProgressCreate)
    assert quality_completion.step_id == "evaluate_quality"
    assert quality_completion.step_status is not None
    assert quality_completion.step_status.value == "COMPLETED"


@pytest.mark.asyncio
async def test_plan_for_another_task_fails_closed_before_any_agent_call() -> None:
    repository = _Repository()
    agents = _Agents()

    result = await TaskOrchestrator(repository, agents, _Planner(OTHER_TASK_ID)).run(
        TASK_ID,
        attempt=1,
    )

    assert result.status is TaskStatus.FAILED
    assert result.last_error is not None
    assert result.last_error.code is ErrorCode.INVALID_PLAN
    assert agents.commands == []


@pytest.mark.asyncio
async def test_unexpected_planner_failure_is_sanitized_and_persisted() -> None:
    repository = _Repository()
    agents = _Agents()

    result = await TaskOrchestrator(repository, agents, _FailingPlanner()).run(
        TASK_ID,
        attempt=1,
    )

    assert result.status is TaskStatus.FAILED
    assert result.last_error is not None
    assert result.last_error.code is ErrorCode.INTERNAL_ERROR
    assert result.last_error.retryable is True
    assert "private planner detail" not in result.last_error.message
    assert agents.commands == []
