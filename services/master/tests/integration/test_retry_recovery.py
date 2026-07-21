from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4, uuid5

import pytest
import pytest_asyncio
from hennongxi_contracts import (
    AgentName,
    AnalysisRunCommand,
    AnalysisRunResult,
    AreaStatistics,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    CreateTaskRequest,
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
    TaskResponse,
    TaskStatus,
    TileArtifactType,
    TileLegendEntry,
    TileMetadata,
)
from hennongxi_master.agent_client import AgentCallError
from hennongxi_master.orchestrator import PlanningOutcome, TaskOrchestrator
from hennongxi_master.planning import build_builtin_recovery_plan
from hennongxi_master.repository import (
    ProgressCreate,
    TaskRepository,
    TransitionCreate,
    WatershedCreate,
    WorkerClaimRequest,
)
from hennongxi_master.worker import OrchestrationWorker, WorkerConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="PostGIS integration test")

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WATERSHED_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
RECOVERED_AT = NOW + timedelta(seconds=31)
SHA256 = "a" * 64


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert DATABASE_URL is not None
    value = create_async_engine(DATABASE_URL)
    async with value.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE watersheds RESTART IDENTITY CASCADE"))
    try:
        yield value
    finally:
        async with value.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE watersheds RESTART IDENTITY CASCADE"))
        await value.dispose()


async def _create_task(repository: TaskRepository) -> None:
    await repository.create_watershed(
        WatershedCreate(
            watershed_id=WATERSHED_ID,
            slug="shennongxi",
            name="神农溪流域",
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [
                        [110.1, 31.0],
                        [110.6, 31.0],
                        [110.6, 31.5],
                        [110.1, 31.5],
                        [110.1, 31.0],
                    ]
                ],
            },
            source_metadata={"product_id": "hybas_as_lev12_v1c"},
            created_at=NOW,
        )
    )
    await repository.create_task(
        task_id=TASK_ID,
        correlation_id=CORRELATION_ID,
        watershed_id=WATERSHED_ID,
        request=CreateTaskRequest(query="分析神农溪植被变化"),
        created_at=NOW,
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


def _artifact(artifact_type: ArtifactType, attempt: int) -> ArtifactRef:
    media_type = {
        ArtifactType.AREA_STATISTICS: "application/json",
        ArtifactType.QUALITY_REPORT: "application/json",
        ArtifactType.PDF_REPORT: "application/pdf",
    }.get(artifact_type, "image/tiff; application=geotiff")
    return ArtifactRef(
        artifact_id=uuid5(TASK_ID, f"{attempt}:{artifact_type.value}"),
        task_id=TASK_ID,
        attempt=attempt,
        artifact_type=artifact_type,
        status=ArtifactStatus.COMPLETE,
        media_type=media_type,
        created_at=NOW,
        checksum_sha256=SHA256,
        byte_size=10,
    )


def _analysis_artifacts(attempt: int) -> tuple[ArtifactRef, ...]:
    return tuple(
        _artifact(artifact_type, attempt)
        for artifact_type in (
            ArtifactType.NDVI_BEFORE,
            ArtifactType.NDVI_AFTER,
            ArtifactType.NDVI_DIFFERENCE,
            ArtifactType.CHANGE_CLASSIFICATION,
            ArtifactType.AREA_STATISTICS,
        )
    )


def _quality_metrics() -> QualityMetrics:
    return QualityMetrics(
        coverage_ratio=0.98,
        valid_pixel_ratio=0.96,
        output_complete=True,
        elapsed_ms=25,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.90,
        ),
        conclusion=QualityConclusion.PASS,
        passed=True,
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


class _Planner:
    def __init__(self) -> None:
        self.calls = 0

    async def create_plan(self, task: TaskResponse) -> PlanningOutcome:
        self.calls += 1
        return PlanningOutcome(
            plan=build_builtin_recovery_plan(
                task_id=task.task_id,
                plan_id=uuid4(),
                created_at=NOW,
            )
        )


class _Agents:
    def __init__(self, fail_once_at: AgentName | None = None) -> None:
        self.fail_once_at = fail_once_at
        self.failed = False
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
        if self.fail_once_at is agent and not self.failed:
            self.failed = True
            code = {
                AgentName.DATA: ErrorCode.DATA_INVALID,
                AgentName.ANALYSIS: ErrorCode.ANALYSIS_FAILED,
                AgentName.QUALITY: ErrorCode.QUALITY_FAILED,
                AgentName.PUBLISHER: ErrorCode.PUBLISHING_FAILED,
            }[agent]
            raise AgentCallError(
                agent=agent,
                step_id=command.step_id,
                error=StructuredError(
                    code=code,
                    message=f"{agent.value} Agent 强制失败",
                    retryable=True,
                ),
                elapsed_ms=10,
            )

    async def prepare_data(self, command: DataPrepareCommand) -> DataPrepareResult:
        self._accept(AgentName.DATA, command)
        return DataPrepareResult(
            task_id=TASK_ID,
            step_id=command.step_id,
            attempt=command.attempt,
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
            attempt=command.attempt,
            correlation_id=CORRELATION_ID,
            artifacts=_analysis_artifacts(command.attempt),
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
            attempt=command.attempt,
            correlation_id=CORRELATION_ID,
            metrics=_quality_metrics(),
            artifact=_artifact(ArtifactType.QUALITY_REPORT, command.attempt),
        )

    async def publish_results(
        self,
        command: PublisherPublishCommand,
        *,
        idempotency_key: UUID,
    ) -> PublisherPublishResult:
        assert idempotency_key.version == 5
        self._accept(AgentName.PUBLISHER, command)
        report = _artifact(ArtifactType.PDF_REPORT, command.attempt)
        tiles = tuple(
            PublishedResource(
                artifact_id=_artifact(
                    ArtifactType(artifact_type.value), command.attempt
                ).artifact_id,
                tile_template=(
                    f"/api/v1/tiles/{TASK_ID}/{artifact_type.value}/{{z}}/{{x}}/{{y}}.png"
                ),
                tile_metadata=_tile_metadata(artifact_type),
            )
            for artifact_type in TileArtifactType
        )
        return PublisherPublishResult(
            task_id=TASK_ID,
            step_id=command.step_id,
            attempt=command.attempt,
            correlation_id=CORRELATION_ID,
            resources=(
                *tiles,
                PublishedResource(
                    artifact_id=report.artifact_id,
                    download_path=(
                        f"/api/v1/tasks/{TASK_ID}/artifacts/{report.artifact_id}/download"
                    ),
                ),
            ),
            report=report,
        )


class _RejectingReuseAgents(_Agents):
    def __init__(self) -> None:
        super().__init__(fail_once_at=AgentName.QUALITY)
        self.rejected_reuse = False

    async def run_analysis(
        self,
        command: AnalysisRunCommand,
        *,
        idempotency_key: UUID,
    ) -> AnalysisRunResult:
        if command.reuse_from_attempt is not None and not self.rejected_reuse:
            self.commands.append(command)
            self.rejected_reuse = True
            raise AgentCallError(
                agent=AgentName.ANALYSIS,
                step_id=command.step_id,
                error=StructuredError(
                    code=ErrorCode.ANALYSIS_FAILED,
                    message="历史分析成果校验失败",
                    retryable=True,
                ),
                elapsed_ms=10,
            )
        return await super().run_analysis(command, idempotency_key=idempotency_key)


async def _step_rows(engine: AsyncEngine) -> tuple[tuple[int, str, str], ...]:
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT attempt, step_id, status FROM steps "
                        "WHERE task_id = :task_id ORDER BY attempt, position"
                    ),
                    {"task_id": TASK_ID},
                )
            )
            .tuples()
            .all()
        )
    return tuple((int(attempt), str(step_id), str(status)) for attempt, step_id, status in rows)


@pytest.mark.parametrize(
    ("failure", "expected_skipped"),
    (
        (AgentName.DATA, frozenset()),
        (AgentName.ANALYSIS, frozenset({"prepare_data"})),
        (
            AgentName.QUALITY,
            frozenset({"prepare_data", "analyze_ndvi_change"}),
        ),
        (
            AgentName.PUBLISHER,
            frozenset({"prepare_data", "analyze_ndvi_change", "evaluate_quality"}),
        ),
    ),
)
@pytest.mark.asyncio
async def test_each_agent_failure_retries_from_expected_checkpoint(
    engine: AsyncEngine,
    failure: AgentName,
    expected_skipped: frozenset[str],
) -> None:
    repository = TaskRepository(engine)
    await _create_task(repository)
    agents = _Agents(fail_once_at=failure)
    planner = _Planner()

    failed = await TaskOrchestrator(
        repository,
        agents,
        planner,
        now=lambda: NOW,
    ).run(TASK_ID, attempt=1)
    history_before_retry = await repository.list_events(TASK_ID)
    accepted = await repository.retry_failed_task(
        TASK_ID,
        accepted_at=NOW + timedelta(seconds=1),
    )
    completed = await TaskOrchestrator(
        repository,
        agents,
        planner,
        now=lambda: NOW + timedelta(seconds=2),
    ).run(TASK_ID, attempt=accepted.response.attempt)

    assert failed.status is TaskStatus.FAILED
    assert completed.status is TaskStatus.COMPLETED
    assert completed.current_attempt == 2
    assert planner.calls == 1
    history_after_retry = await repository.list_events(TASK_ID)
    assert history_after_retry[: len(history_before_retry)] == history_before_retry
    assert {event.attempt for event in history_after_retry} == {1, 2}
    current_skipped = {
        step_id
        for attempt, step_id, status in await _step_rows(engine)
        if attempt == 2 and status == StepStatus.SKIPPED.value
    }
    assert current_skipped == expected_skipped
    completed_counts = {
        step_id: sum(
            status == StepStatus.COMPLETED.value
            for _, candidate, status in await _step_rows(engine)
            if candidate == step_id
        )
        for step_id in (
            "prepare_data",
            "analyze_ndvi_change",
            "evaluate_quality",
            "publish_results",
        )
    }
    assert completed_counts == {
        "prepare_data": 1,
        "analyze_ndvi_change": 1,
        "evaluate_quality": 1,
        "publish_results": 1,
    }
    attempt_two_analysis = next(
        command
        for command in agents.commands
        if isinstance(command, AnalysisRunCommand) and command.attempt == 2
    )
    attempt_two_quality = next(
        command
        for command in agents.commands
        if isinstance(command, QualityEvaluateCommand) and command.attempt == 2
    )
    assert attempt_two_analysis.reuse_from_attempt == (
        1 if failure in {AgentName.QUALITY, AgentName.PUBLISHER} else None
    )
    assert attempt_two_quality.reuse_from_attempt == (1 if failure is AgentName.PUBLISHER else None)


@pytest.mark.asyncio
async def test_invalid_upstream_checkpoint_is_recomputed_in_same_retry(
    engine: AsyncEngine,
) -> None:
    repository = TaskRepository(engine)
    await _create_task(repository)
    agents = _RejectingReuseAgents()
    planner = _Planner()

    failed = await TaskOrchestrator(repository, agents, planner, now=lambda: NOW).run(
        TASK_ID,
        attempt=1,
    )
    accepted = await repository.retry_failed_task(
        TASK_ID,
        accepted_at=NOW + timedelta(seconds=1),
    )
    completed = await TaskOrchestrator(
        repository,
        agents,
        planner,
        now=lambda: NOW + timedelta(seconds=2),
    ).run(TASK_ID, attempt=accepted.response.attempt)

    assert failed.status is TaskStatus.FAILED
    assert completed.status is TaskStatus.COMPLETED
    retry_analysis_commands = [
        command
        for command in agents.commands
        if isinstance(command, AnalysisRunCommand) and command.attempt == 2
    ]
    assert [command.reuse_from_attempt for command in retry_analysis_commands] == [1, None]
    retry_quality = next(
        command
        for command in agents.commands
        if isinstance(command, QualityEvaluateCommand) and command.attempt == 2
    )
    assert retry_quality.reuse_from_attempt is None
    rows = await _step_rows(engine)
    assert (
        next(
            status
            for attempt, step_id, status in rows
            if attempt == 2 and step_id == "analyze_ndvi_change"
        )
        == StepStatus.COMPLETED.value
    )


async def _seed_interrupted_analysis(repository: TaskRepository) -> None:
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="planning",
            agent=AgentName.MASTER,
            target_status=TaskStatus.PLANNING,
            progress=5,
            message="正在生成计划",
            elapsed_ms=1,
            occurred_at=NOW + timedelta(seconds=1),
        )
    )
    await repository.save_plan(
        build_builtin_recovery_plan(task_id=TASK_ID, plan_id=uuid4(), created_at=NOW),
        attempt=1,
    )
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.DATA_PREPARING,
            progress=10,
            message="开始准备数据",
            elapsed_ms=1,
            occurred_at=NOW + timedelta(seconds=2),
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=NOW + timedelta(seconds=2),
        )
    )
    data = DataPrepareResult(
        task_id=TASK_ID,
        step_id="prepare_data",
        attempt=1,
        correlation_id=CORRELATION_ID,
        assets=_assets(),
    )
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.ANALYZING,
            progress=25,
            message="数据准备完成",
            elapsed_ms=2,
            occurred_at=NOW + timedelta(seconds=3),
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=NOW + timedelta(seconds=3),
            step_output=data,
        )
    )
    await repository.record_progress(
        ProgressCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="analyze_ndvi_change",
            agent=AgentName.ANALYSIS,
            target_status=TaskStatus.ANALYZING,
            progress=30,
            message="开始分析",
            elapsed_ms=0,
            occurred_at=NOW + timedelta(seconds=4),
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=NOW + timedelta(seconds=4),
        )
    )


@pytest.mark.asyncio
async def test_master_restart_during_analysis_reaches_one_correct_terminal_path(
    engine: AsyncEngine,
) -> None:
    repository = TaskRepository(engine)
    await _create_task(repository)
    original_claim = await repository.claim_next_task(
        WorkerClaimRequest(worker_id="master-old", claimed_at=NOW, lease_seconds=30)
    )
    assert original_claim is not None
    await _seed_interrupted_analysis(repository)
    planner = _Planner()
    orchestrator = TaskOrchestrator(
        repository,
        _Agents(),
        planner,
        now=lambda: RECOVERED_AT,
    )
    worker = OrchestrationWorker(
        repository,
        orchestrator,
        WorkerConfig(
            worker_id="master-new",
            poll_interval_seconds=0.01,
            lease_seconds=30,
            heartbeat_interval_seconds=10,
        ),
        now=lambda: RECOVERED_AT,
    )

    assert await worker.run_once() is True
    recovered = await repository.get_task(TASK_ID)
    assert recovered is not None
    assert recovered.status is TaskStatus.PENDING
    assert recovered.current_attempt == 2
    assert await worker.run_once() is True

    completed = await repository.get_task(TASK_ID)
    events = await repository.list_events(TASK_ID)
    assert completed is not None
    assert completed.status is TaskStatus.COMPLETED
    assert completed.current_attempt == 2
    assert planner.calls == 0
    assert any(
        event.attempt == 1
        and event.status is TaskStatus.FAILED
        and event.step_id == "analyze_ndvi_change"
        for event in events
    )
    rows = await _step_rows(engine)
    assert {
        step_id
        for attempt, step_id, status in rows
        if attempt == 2 and status == StepStatus.SKIPPED.value
    } == {"prepare_data"}
    assert all(
        sum(
            status == StepStatus.COMPLETED.value
            for _, candidate, status in rows
            if candidate == step_id
        )
        == 1
        for step_id in (
            "prepare_data",
            "analyze_ndvi_change",
            "evaluate_quality",
            "publish_results",
        )
    )
