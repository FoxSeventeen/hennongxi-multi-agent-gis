from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from hennongxi_contracts import (
    AgentName,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    CreateTaskRequest,
    ExecutionPlan,
    ModelCallRecord,
    ModelCallStatus,
    PlanSource,
    PlanStep,
    PlanStepKind,
    StepStatus,
    TaskStatus,
)
from hennongxi_contracts.state import InvalidTaskTransition
from hennongxi_master.llm import LlmConfig, LlmPlanningAdapter, LlmPlanningError
from hennongxi_master.planning import build_builtin_recovery_plan
from hennongxi_master.repository import (
    ArtifactCreate,
    ProgressCreate,
    RepositoryConflict,
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
        columns = set(
            await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'model_calls'"
                )
            )
        )
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
