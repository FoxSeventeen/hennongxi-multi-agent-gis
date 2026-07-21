from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from hennongxi_contracts import (
    AgentName,
    AnalysisRunResult,
    AreaStatistics,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    CreateTaskRequest,
    DataAssetRef,
    DataPrepareResult,
    ErrorCode,
    ExecutionPlan,
    LogicalDatasetId,
    ModelCallRecord,
    ModelCallStatus,
    PlanSource,
    PlanStep,
    PlanStepKind,
    QualityConclusion,
    QualityEvaluateResult,
    QualityMetrics,
    QualityThresholds,
    StepStatus,
    StructuredError,
    TaskStatus,
)
from hennongxi_contracts.state import InvalidTaskTransition
from hennongxi_master.llm import LlmConfig, LlmPlanningAdapter, LlmPlanningError
from hennongxi_master.planning import build_builtin_recovery_plan
from hennongxi_master.repository import (
    ArtifactCreate,
    ProgressCreate,
    RepositoryConflict,
    RepositoryNotFound,
    TaskRepository,
    TransitionCreate,
    WatershedCreate,
    WorkerClaimRequest,
    WorkerLeaseRenewal,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="PostGIS integration test")

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WATERSHED_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
PLAN_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CORRELATION_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
ARTIFACT_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
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


@pytest_asyncio.fixture
async def repository(engine: AsyncEngine) -> AsyncIterator[TaskRepository]:
    value = TaskRepository(engine)
    yield value


def watershed() -> WatershedCreate:
    return WatershedCreate(
        watershed_id=WATERSHED_ID,
        slug="shennongxi",
        name="神农溪流域",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [
                    [110.1, 31.0],
                    [110.5, 31.0],
                    [110.5, 31.4],
                    [110.1, 31.4],
                    [110.1, 31.0],
                ]
            ],
        },
        source_metadata={"product_id": "hybas_as_lev12_v1c"},
        created_at=NOW,
    )


def plan() -> ExecutionPlan:
    definitions = (
        ("prepare_data", PlanStepKind.PREPARE_DATA, AgentName.DATA, ()),
        (
            "analyze_ndvi_change",
            PlanStepKind.ANALYZE_NDVI_CHANGE,
            AgentName.ANALYSIS,
            ("prepare_data",),
        ),
        (
            "evaluate_quality",
            PlanStepKind.EVALUATE_QUALITY,
            AgentName.QUALITY,
            ("analyze_ndvi_change",),
        ),
        (
            "publish_results",
            PlanStepKind.PUBLISH_RESULTS,
            AgentName.PUBLISHER,
            ("evaluate_quality",),
        ),
    )
    steps = tuple(
        PlanStep(
            step_id=step_id,
            kind=kind,
            agent=agent,
            order=position,
            title=step_id,
            depends_on=depends_on,
        )
        for position, (step_id, kind, agent, depends_on) in enumerate(definitions, start=1)
    )
    return ExecutionPlan(
        plan_id=PLAN_ID,
        task_id=TASK_ID,
        source=PlanSource.REAL_LLM,
        created_at=NOW,
        model_call=ModelCallRecord(
            model="approved-model",
            started_at=NOW,
            duration_ms=125,
            status=ModelCallStatus.SUCCEEDED,
            input_tokens=100,
            output_tokens=50,
            response_sha256=SHA256,
        ),
        steps=steps,
    )


async def create_pending_task(repository: TaskRepository) -> None:
    await repository.create_watershed(watershed())
    await repository.create_task(
        task_id=TASK_ID,
        correlation_id=CORRELATION_ID,
        watershed_id=WATERSHED_ID,
        request=CreateTaskRequest(query="分析神农溪植被变化"),
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_repository_persists_and_reconstructs_complete_current_graph(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="planning",
            agent=AgentName.MASTER,
            target_status=TaskStatus.PLANNING,
            progress=5,
            message="正在生成计划",
            elapsed_ms=10,
            occurred_at=NOW,
        )
    )
    await repository.save_plan(plan(), attempt=1)
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.DATA_PREPARING,
            progress=10,
            message="正在准备数据",
            elapsed_ms=20,
            occurred_at=NOW,
            step_status=StepStatus.RUNNING,
            step_progress=10,
            step_started_at=NOW,
        )
    )
    artifact = ArtifactRef(
        artifact_id=ARTIFACT_ID,
        task_id=TASK_ID,
        attempt=1,
        artifact_type=ArtifactType.DATA_MANIFEST,
        status=ArtifactStatus.COMPLETE,
        media_type="application/json",
        created_at=NOW,
        checksum_sha256=SHA256,
        byte_size=512,
    )
    await repository.record_artifact(
        ArtifactCreate(
            artifact=artifact,
            step_id="prepare_data",
            storage_key=f"{TASK_ID}/attempt-1/data_manifest.json",
        )
    )
    data_output = DataPrepareResult(
        task_id=TASK_ID,
        step_id="prepare_data",
        attempt=1,
        correlation_id=CORRELATION_ID,
        assets=tuple(
            DataAssetRef(
                dataset_id=dataset_id,
                checksum_sha256=SHA256,
                byte_size=10,
            )
            for dataset_id in LogicalDatasetId
        ),
    )
    completed_event = await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.ANALYZING,
            progress=25,
            message="数据准备完成",
            elapsed_ms=30,
            occurred_at=NOW,
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=NOW,
            artifact_ids=(ARTIFACT_ID,),
            step_output=data_output,
        )
    )
    assert completed_event.sequence == 3
    assert completed_event.artifacts == (artifact,)

    await repository.dispose()
    reconnected = TaskRepository(engine)
    reconstructed = await reconnected.get_task(TASK_ID)
    events = await reconnected.list_events(TASK_ID)
    geometry = await reconnected.get_watershed_geometry(WATERSHED_ID)

    assert reconstructed is not None
    assert reconstructed.status is TaskStatus.ANALYZING
    assert reconstructed.progress == 25
    assert reconstructed.plan == plan()
    assert reconstructed.artifacts == (artifact,)
    assert reconstructed.steps[0].status is StepStatus.COMPLETED
    assert reconstructed.steps[0].artifacts == (artifact,)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.sequence for event in await reconnected.list_events(TASK_ID, limit=1)] == [1]
    assert [
        event.sequence
        for event in await reconnected.list_events(TASK_ID, after_sequence=1, limit=1)
    ] == [2]
    assert geometry["type"] == "MultiPolygon"

    async with engine.connect() as connection:
        stored_output = await connection.scalar(
            text(
                "SELECT output FROM steps WHERE task_id = :task_id "
                "AND attempt = 1 AND step_id = 'prepare_data'"
            ),
            {"task_id": TASK_ID},
        )
        columns = set(
            await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'model_calls'"
                )
            )
        )
    assert DataPrepareResult.model_validate(stored_output) == data_output
    assert {"api_key", "prompt", "response_body"}.isdisjoint(columns)


@pytest.mark.asyncio
async def test_repository_atomically_persists_only_sanitized_failed_llm_evidence(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)
    private_credential = "private-provider-credential"
    private_base_url = "https://private-provider.example/v1"
    private_response = "private-provider-response-body"
    config = LlmConfig.from_environment(
        {
            "LLM_API_KEY": private_credential,
            "LLM_BASE_URL": private_base_url,
            "LLM_MODEL": "approved-model",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=private_response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LlmPlanningError) as raised:
            await LlmPlanningAdapter(config, client).create_plan(
                task_id=TASK_ID,
                query="分析神农溪植被变化",
            )

    recovery_plan = build_builtin_recovery_plan(
        task_id=TASK_ID,
        plan_id=PLAN_ID,
        created_at=NOW,
    )
    await repository.save_plan(
        recovery_plan,
        attempt=1,
        failed_model_call=raised.value.model_call,
    )

    async with engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("SELECT * FROM model_calls WHERE plan_id = :plan_id"),
                    {"plan_id": PLAN_ID},
                )
            )
            .mappings()
            .one()
        )
    persisted = json.dumps(dict(row), default=str)
    reconstructed = await repository.get_task(TASK_ID)

    assert reconstructed is not None
    assert reconstructed.plan == recovery_plan
    assert row["model"] == "approved-model"
    assert row["status"] == "FAILED"
    assert row["error_code"] == "LLM_AUTHENTICATION_FAILED"
    assert row["response_sha256"] is None
    for private_value in (private_credential, private_base_url, private_response):
        assert private_value not in persisted
        assert private_value not in reconstructed.model_dump_json()


@pytest.mark.asyncio
async def test_repository_rejects_mismatched_recovery_evidence_without_partial_rows(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)
    recovery_plan = build_builtin_recovery_plan(
        task_id=TASK_ID,
        plan_id=PLAN_ID,
        created_at=NOW,
    )
    succeeded_call = plan().model_call
    assert succeeded_call is not None

    with pytest.raises(ValueError, match="failed_model_call"):
        await repository.save_plan(
            recovery_plan,
            attempt=1,
            failed_model_call=succeeded_call,
        )

    async with engine.connect() as connection:
        plan_count = await connection.scalar(text("SELECT count(*) FROM plans"))
        call_count = await connection.scalar(text("SELECT count(*) FROM model_calls"))
        step_count = await connection.scalar(text("SELECT count(*) FROM steps"))
    assert (plan_count, call_count, step_count) == (0, 0, 0)


@pytest.mark.asyncio
async def test_illegal_transition_rolls_back_task_and_event_together(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)

    with pytest.raises(InvalidTaskTransition):
        await repository.transition_task(
            TransitionCreate(
                task_id=TASK_ID,
                attempt=1,
                step_id="analyze_ndvi_change",
                agent=AgentName.ANALYSIS,
                target_status=TaskStatus.ANALYZING,
                progress=50,
                message="非法跳过状态",
                elapsed_ms=1,
                occurred_at=NOW,
            )
        )

    reconnected = TaskRepository(engine)
    task = await reconnected.get_task(TASK_ID)
    events = await reconnected.list_events(TASK_ID)

    assert task is not None
    assert task.status is TaskStatus.PENDING
    assert task.progress == 0
    assert events == ()


@pytest.mark.asyncio
async def test_same_state_progress_atomically_updates_running_step_and_event(
    repository: TaskRepository,
) -> None:
    await create_pending_task(repository)
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="planning",
            agent=AgentName.MASTER,
            target_status=TaskStatus.PLANNING,
            progress=5,
            message="正在生成计划",
            elapsed_ms=10,
            occurred_at=NOW,
        )
    )
    await repository.save_plan(plan(), attempt=1)
    started_at = NOW + timedelta(seconds=1)
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.DATA_PREPARING,
            progress=10,
            message="开始准备数据",
            elapsed_ms=0,
            occurred_at=started_at,
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=started_at,
        )
    )

    event = await repository.record_progress(
        ProgressCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="prepare_data",
            agent=AgentName.DATA,
            target_status=TaskStatus.DATA_PREPARING,
            progress=25,
            message="数据校验进行中",
            elapsed_ms=500,
            occurred_at=NOW + timedelta(seconds=2),
            step_status=StepStatus.RUNNING,
            step_progress=60,
        )
    )

    task = await repository.get_task(TASK_ID)
    assert task is not None
    assert task.status is TaskStatus.DATA_PREPARING
    assert task.progress == 25
    assert task.steps[0].status is StepStatus.RUNNING
    assert task.steps[0].progress == 60
    assert task.steps[0].started_at == started_at
    assert event.sequence == 3
    assert event.status is TaskStatus.DATA_PREPARING
    assert event.correlation_id == CORRELATION_ID


@pytest.mark.asyncio
async def test_same_state_progress_rejects_stale_state_without_partial_writes(
    repository: TaskRepository,
) -> None:
    await create_pending_task(repository)
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="planning",
            agent=AgentName.MASTER,
            target_status=TaskStatus.PLANNING,
            progress=5,
            message="正在生成计划",
            elapsed_ms=10,
            occurred_at=NOW,
        )
    )
    await repository.save_plan(plan(), attempt=1)

    with pytest.raises(RepositoryConflict, match="task status changed"):
        await repository.record_progress(
            ProgressCreate(
                task_id=TASK_ID,
                attempt=1,
                step_id="prepare_data",
                agent=AgentName.DATA,
                target_status=TaskStatus.DATA_PREPARING,
                progress=10,
                message="过期工作者不得写入",
                elapsed_ms=1,
                occurred_at=NOW + timedelta(seconds=1),
                step_status=StepStatus.RUNNING,
                step_progress=1,
                step_started_at=NOW + timedelta(seconds=1),
            )
        )

    task = await repository.get_task(TASK_ID)
    assert task is not None
    assert task.status is TaskStatus.PLANNING
    assert task.progress == 5
    assert task.steps[0].status is StepStatus.PENDING
    assert [event.sequence for event in await repository.list_events(TASK_ID)] == [1]


@pytest.mark.asyncio
async def test_artifact_batch_rolls_back_every_item_when_one_step_is_invalid(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)
    await repository.save_plan(plan(), attempt=1)
    first = ArtifactRef(
        artifact_id=ARTIFACT_ID,
        task_id=TASK_ID,
        attempt=1,
        artifact_type=ArtifactType.NDVI_BEFORE,
        status=ArtifactStatus.COMPLETE,
        media_type="image/tiff; application=geotiff",
        created_at=NOW,
        checksum_sha256=SHA256,
        byte_size=10,
    )
    second = first.model_copy(
        update={
            "artifact_id": UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            "artifact_type": ArtifactType.NDVI_AFTER,
        }
    )

    with pytest.raises(IntegrityError):
        await repository.record_artifacts(
            (
                ArtifactCreate(
                    artifact=first,
                    step_id="analyze_ndvi_change",
                    storage_key=f"{TASK_ID}/attempt-1/analysis/ndvi_before",
                ),
                ArtifactCreate(
                    artifact=second,
                    step_id="missing_step",
                    storage_key=f"{TASK_ID}/attempt-1/analysis/ndvi_after",
                ),
            )
        )

    async with engine.connect() as connection:
        artifact_count = await connection.scalar(text("SELECT count(*) FROM artifacts"))
    assert artifact_count == 0


async def assert_constraint_rejects(
    engine: AsyncEngine,
    statement: str,
    parameters: dict[str, object] | None = None,
) -> None:
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text(statement), parameters or {})


@pytest.mark.asyncio
async def test_database_constraints_reject_invalid_and_orphaned_workflow_rows(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)

    await assert_constraint_rejects(
        engine,
        "UPDATE tasks SET progress = 101 WHERE task_id = :task_id",
        {"task_id": TASK_ID},
    )
    await assert_constraint_rejects(
        engine,
        "UPDATE tasks SET status = 'UNKNOWN' WHERE task_id = :task_id",
        {"task_id": TASK_ID},
    )

    await repository.save_plan(plan(), attempt=1)
    await assert_constraint_rejects(
        engine,
        "UPDATE steps SET depends_on_step_id = 'missing_step' "
        "WHERE task_id = :task_id AND attempt = 1 AND step_id = 'analyze_ndvi_change'",
        {"task_id": TASK_ID},
    )
    await assert_constraint_rejects(
        engine,
        "INSERT INTO steps SELECT * FROM steps "
        "WHERE task_id = :task_id AND attempt = 1 AND step_id = 'prepare_data'",
        {"task_id": TASK_ID},
    )
    await assert_constraint_rejects(
        engine,
        "INSERT INTO artifacts "
        "(artifact_id, task_id, attempt, step_id, artifact_type, status, media_type, "
        "storage_key, checksum_sha256, byte_size, created_at) "
        "VALUES (:artifact_id, :task_id, 1, 'missing_step', 'DATA_MANIFEST', "
        "'COMPLETE', 'application/json', 'missing/data.json', :checksum, 1, :created_at)",
        {
            "artifact_id": UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            "task_id": TASK_ID,
            "checksum": SHA256,
            "created_at": NOW,
        },
    )
    await assert_constraint_rejects(
        engine,
        "INSERT INTO events "
        "(task_id, attempt, step_id, correlation_id, agent, status, progress, message, "
        "elapsed_ms, occurred_at) "
        "VALUES (:task_id, 99, 'planning', :correlation_id, 'master', 'PLANNING', "
        "5, '孤立事件', 1, :occurred_at)",
        {
            "task_id": TASK_ID,
            "correlation_id": CORRELATION_ID,
            "occurred_at": NOW,
        },
    )


@pytest.mark.asyncio
async def test_failed_task_retry_is_atomic_and_concurrent_requests_share_one_attempt(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)
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
            occurred_at=NOW,
        )
    )
    await repository.save_plan(plan(), attempt=1)
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
            occurred_at=NOW + timedelta(seconds=1),
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=NOW + timedelta(seconds=1),
        )
    )
    failure = StructuredError(
        code=ErrorCode.ANALYSIS_FAILED,
        message="分析服务暂时失败",
        retryable=True,
    )
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="analyze_ndvi_change",
            agent=AgentName.ANALYSIS,
            target_status=TaskStatus.FAILED,
            progress=30,
            message="分析失败",
            elapsed_ms=2,
            occurred_at=NOW + timedelta(seconds=2),
            error=failure,
        )
    )

    competitor = TaskRepository(engine)
    accepted_at = NOW + timedelta(seconds=3)
    first, duplicate = await asyncio.gather(
        repository.retry_failed_task(TASK_ID, accepted_at=accepted_at),
        competitor.retry_failed_task(TASK_ID, accepted_at=accepted_at),
    )

    assert first.response == duplicate.response
    assert first.response.attempt == 2
    assert first.response.status is TaskStatus.PENDING
    assert {first.created, duplicate.created} == {False, True}
    task = await repository.get_task(TASK_ID)
    events = await repository.list_events(TASK_ID)
    assert task is not None
    assert task.current_attempt == 2
    assert task.status is TaskStatus.PENDING
    assert task.progress == 0
    assert task.last_error is None
    assert [event.attempt for event in events] == [1, 1, 1, 2]
    assert events[-1].step_id == "analyze_ndvi_change"
    assert events[-1].message == "已接受失败任务重试"

    async with engine.connect() as connection:
        attempts = (
            (
                await connection.execute(
                    text(
                        "SELECT attempt, status, resume_from_step_id FROM attempts "
                        "WHERE task_id = :task_id ORDER BY attempt"
                    ),
                    {"task_id": TASK_ID},
                )
            )
            .tuples()
            .all()
        )
    assert attempts == [(1, "FAILED", None), (2, "PENDING", "analyze_ndvi_change")]


@pytest.mark.asyncio
async def test_retry_snapshot_loads_validated_upstream_outputs_without_new_model_call(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)
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
            occurred_at=NOW,
        )
    )
    source_plan = plan()
    await repository.save_plan(source_plan, attempt=1)
    data = DataPrepareResult(
        task_id=TASK_ID,
        step_id="prepare_data",
        attempt=1,
        correlation_id=CORRELATION_ID,
        assets=tuple(
            DataAssetRef(
                dataset_id=dataset_id,
                checksum_sha256=SHA256,
                byte_size=10,
            )
            for dataset_id in LogicalDatasetId
        ),
    )
    analysis_artifacts = tuple(
        ArtifactRef(
            artifact_id=uuid4(),
            task_id=TASK_ID,
            attempt=1,
            artifact_type=artifact_type,
            status=ArtifactStatus.COMPLETE,
            media_type=(
                "application/json"
                if artifact_type is ArtifactType.AREA_STATISTICS
                else "image/tiff; application=geotiff"
            ),
            created_at=NOW,
            checksum_sha256=SHA256,
            byte_size=10,
        )
        for artifact_type in (
            ArtifactType.NDVI_BEFORE,
            ArtifactType.NDVI_AFTER,
            ArtifactType.NDVI_DIFFERENCE,
            ArtifactType.CHANGE_CLASSIFICATION,
            ArtifactType.AREA_STATISTICS,
        )
    )
    analysis = AnalysisRunResult(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=analysis_artifacts,
        statistics=AreaStatistics(
            increase_hectares=10,
            stable_hectares=20,
            decrease_hectares=5,
            valid_hectares=35,
        ),
        elapsed_ms=120,
    )
    quality = QualityEvaluateResult(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        metrics=QualityMetrics(
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
        ),
        artifact=ArtifactRef(
            artifact_id=uuid4(),
            task_id=TASK_ID,
            attempt=1,
            artifact_type=ArtifactType.QUALITY_REPORT,
            status=ArtifactStatus.COMPLETE,
            media_type="application/json",
            created_at=NOW,
            checksum_sha256=SHA256,
            byte_size=10,
        ),
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
            occurred_at=NOW + timedelta(seconds=1),
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=NOW + timedelta(seconds=1),
        )
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
            occurred_at=NOW + timedelta(seconds=2),
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=NOW + timedelta(seconds=2),
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
            elapsed_ms=1,
            occurred_at=NOW + timedelta(seconds=3),
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=NOW + timedelta(seconds=3),
        )
    )
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="analyze_ndvi_change",
            agent=AgentName.ANALYSIS,
            target_status=TaskStatus.QUALITY_CHECKING,
            progress=55,
            message="分析完成",
            elapsed_ms=3,
            occurred_at=NOW + timedelta(seconds=4),
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=NOW + timedelta(seconds=4),
            step_output=analysis,
        )
    )
    await repository.record_progress(
        ProgressCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="evaluate_quality",
            agent=AgentName.QUALITY,
            target_status=TaskStatus.QUALITY_CHECKING,
            progress=60,
            message="开始质量检查",
            elapsed_ms=1,
            occurred_at=NOW + timedelta(seconds=5),
            step_status=StepStatus.RUNNING,
            step_progress=0,
            step_started_at=NOW + timedelta(seconds=5),
        )
    )
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="evaluate_quality",
            agent=AgentName.QUALITY,
            target_status=TaskStatus.PUBLISHING,
            progress=75,
            message="质量检查完成",
            elapsed_ms=2,
            occurred_at=NOW + timedelta(seconds=6),
            step_status=StepStatus.COMPLETED,
            step_progress=100,
            step_completed_at=NOW + timedelta(seconds=6),
            step_output=quality,
        )
    )
    visible_results = await repository.get_task(TASK_ID)
    assert visible_results is not None
    assert visible_results.analysis == analysis
    assert visible_results.quality == quality
    await repository.transition_task(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="publish_results",
            agent=AgentName.PUBLISHER,
            target_status=TaskStatus.FAILED,
            progress=80,
            message="发布失败",
            elapsed_ms=1,
            occurred_at=NOW + timedelta(seconds=7),
            error=StructuredError(
                code=ErrorCode.PUBLISHING_FAILED,
                message="发布服务暂时失败",
                retryable=True,
            ),
            step_status=StepStatus.FAILED,
            step_progress=0,
            step_completed_at=NOW + timedelta(seconds=7),
        )
    )
    await repository.retry_failed_task(TASK_ID, accepted_at=NOW + timedelta(seconds=8))

    snapshot = await repository.get_recovery_snapshot(TASK_ID, 2)

    assert snapshot is not None
    assert snapshot.source_attempt == 1
    assert snapshot.resume_from_step_id == "publish_results"
    assert snapshot.plan == source_plan
    assert snapshot.data == data
    assert snapshot.analysis == analysis
    assert snapshot.quality == quality

    reused_plan = source_plan.model_copy(
        update={"plan_id": uuid4(), "created_at": NOW + timedelta(seconds=9)}
    )
    await repository.save_plan(reused_plan, attempt=2, reused=True)
    async with engine.connect() as connection:
        model_call_count = await connection.scalar(text("SELECT count(*) FROM model_calls"))
    assert model_call_count == 1


@pytest.mark.asyncio
async def test_retry_rejects_missing_and_nonfailed_tasks(repository: TaskRepository) -> None:
    with pytest.raises(RepositoryNotFound):
        await repository.retry_failed_task(uuid4(), accepted_at=NOW)

    await create_pending_task(repository)
    with pytest.raises(RepositoryConflict, match="only failed"):
        await repository.retry_failed_task(TASK_ID, accepted_at=NOW)


@pytest.mark.asyncio
async def test_worker_claim_is_exclusive_and_expired_lease_can_be_reclaimed(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)

    first = await repository.claim_next_task(
        WorkerClaimRequest(worker_id="master-a", claimed_at=NOW, lease_seconds=30)
    )
    assert first is not None
    assert first.task_id == TASK_ID
    assert first.worker_id == "master-a"
    assert first.lease_expires_at == NOW + timedelta(seconds=30)

    renewed = await repository.renew_claim(
        first,
        WorkerLeaseRenewal(
            heartbeat_at=NOW + timedelta(seconds=20),
            lease_seconds=30,
        ),
    )
    assert renewed.heartbeat_at == NOW + timedelta(seconds=20)
    assert renewed.lease_expires_at == NOW + timedelta(seconds=50)

    competitor = TaskRepository(engine)
    blocked = await competitor.claim_next_task(
        WorkerClaimRequest(
            worker_id="master-b",
            claimed_at=NOW + timedelta(seconds=31),
            lease_seconds=30,
        )
    )
    assert blocked is None

    replacement = await competitor.claim_next_task(
        WorkerClaimRequest(
            worker_id="master-b",
            claimed_at=NOW + timedelta(seconds=51),
            lease_seconds=30,
        )
    )
    assert replacement is not None
    assert replacement.task_id == TASK_ID
    assert replacement.worker_id == "master-b"

    with pytest.raises(RepositoryConflict):
        await repository.renew_claim(
            first,
            WorkerLeaseRenewal(
                heartbeat_at=NOW + timedelta(seconds=52),
                lease_seconds=30,
            ),
        )

    with pytest.raises(RepositoryConflict):
        await repository.release_claim(
            first,
            released_at=NOW + timedelta(seconds=52),
        )

    released = await competitor.release_claim(
        replacement,
        released_at=NOW + timedelta(seconds=53),
    )
    assert released.released_at == NOW + timedelta(seconds=53)


@pytest.mark.asyncio
async def test_expired_analysis_attempt_is_atomically_failed_and_requeued(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)
    original = await repository.claim_next_task(
        WorkerClaimRequest(worker_id="master-old", claimed_at=NOW, lease_seconds=30)
    )
    assert original is not None
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
    await repository.save_plan(plan(), attempt=1)
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
        assets=tuple(
            DataAssetRef(
                dataset_id=dataset_id,
                checksum_sha256=SHA256,
                byte_size=10,
            )
            for dataset_id in LogicalDatasetId
        ),
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

    replacement_repository = TaskRepository(engine)
    replacement = await replacement_repository.claim_next_task(
        WorkerClaimRequest(
            worker_id="master-new",
            claimed_at=NOW + timedelta(seconds=31),
            lease_seconds=30,
        )
    )
    assert replacement is not None
    recovered = await replacement_repository.recover_interrupted_attempt(
        replacement,
        recovered_at=NOW + timedelta(seconds=31),
    )

    assert recovered is not None
    assert recovered.interrupted_attempt == 1
    assert recovered.retry_attempt == 2
    assert recovered.resume_from_step_id == "analyze_ndvi_change"
    task = await repository.get_task(TASK_ID)
    events = await repository.list_events(TASK_ID)
    snapshot = await repository.get_recovery_snapshot(TASK_ID, 2)
    assert task is not None
    assert task.status is TaskStatus.PENDING
    assert task.current_attempt == 2
    assert events[-2].attempt == 1
    assert events[-2].status is TaskStatus.FAILED
    assert events[-2].error is not None
    assert events[-2].error.code is ErrorCode.INTERNAL_ERROR
    assert events[-1].attempt == 2
    assert events[-1].status is TaskStatus.PENDING
    assert snapshot is not None
    assert snapshot.resume_from_step_id == "analyze_ndvi_change"
    assert snapshot.data == data
    assert snapshot.analysis is None

    async with engine.connect() as connection:
        attempts = (
            (
                await connection.execute(
                    text(
                        "SELECT attempt, status, resume_from_step_id FROM attempts "
                        "WHERE task_id = :task_id ORDER BY attempt"
                    ),
                    {"task_id": TASK_ID},
                )
            )
            .tuples()
            .all()
        )
        analysis_status = await connection.scalar(
            text(
                "SELECT status FROM steps WHERE task_id = :task_id "
                "AND attempt = 1 AND step_id = 'analyze_ndvi_change'"
            ),
            {"task_id": TASK_ID},
        )
    assert attempts == [(1, "FAILED", None), (2, "PENDING", "analyze_ndvi_change")]
    assert analysis_status == "FAILED"

    await replacement_repository.release_claim(
        replacement,
        released_at=NOW + timedelta(seconds=32),
    )
    next_claim = await repository.claim_next_task(
        WorkerClaimRequest(
            worker_id="master-new",
            claimed_at=NOW + timedelta(seconds=32),
            lease_seconds=30,
        )
    )
    assert next_claim is not None
    assert next_claim.attempt == 2


@pytest.mark.asyncio
async def test_concurrent_worker_claims_have_exactly_one_winner(
    repository: TaskRepository,
    engine: AsyncEngine,
) -> None:
    await create_pending_task(repository)
    competitor = TaskRepository(engine)

    claims = await asyncio.gather(
        repository.claim_next_task(
            WorkerClaimRequest(worker_id="master-a", claimed_at=NOW, lease_seconds=30)
        ),
        competitor.claim_next_task(
            WorkerClaimRequest(worker_id="master-b", claimed_at=NOW, lease_seconds=30)
        ),
    )

    winners = tuple(claim for claim in claims if claim is not None)
    assert len(winners) == 1
    assert winners[0].task_id == TASK_ID
