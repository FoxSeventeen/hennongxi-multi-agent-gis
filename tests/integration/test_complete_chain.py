from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from hennongxi_contracts import (
    ArtifactType,
    CreateTaskRequest,
    ErrorCode,
    PlanSource,
    StepStatus,
    TaskEvent,
    TaskResponse,
    TaskStatus,
)
from hennongxi_master.agent_client import AgentClientConfig, AgentHttpClient
from hennongxi_master.amap import AmapConfig, AmapStudyAreaVerifier
from hennongxi_master.llm import LlmConfig, LlmPlanningAdapter
from hennongxi_master.orchestrator import TaskOrchestrator
from hennongxi_master.repository import TaskRepository, WatershedCreate
from hennongxi_master.study_area import StudyAreaGrounder
from hennongxi_master.worker import OrchestrationWorker, RecoveryTaskPlanner, WorkerConfig
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.fixtures.deterministic_gis import (
    DeterministicGisFixture,
    write_deterministic_gis_fixture,
)
from tests.integration.harness import configured_agent_apps, routed_agent_transport

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="PostGIS is required")

TASK_ID = UUID("25252525-2525-4525-8525-252525252525")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
WATERSHED_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
ANALYSIS_TYPES = {
    ArtifactType.NDVI_BEFORE,
    ArtifactType.NDVI_AFTER,
    ArtifactType.NDVI_DIFFERENCE,
    ArtifactType.CHANGE_CLASSIFICATION,
    ArtifactType.AREA_STATISTICS,
}
REQUIRED_TYPES = {
    *ANALYSIS_TYPES,
    ArtifactType.QUALITY_REPORT,
    ArtifactType.PDF_REPORT,
}


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert DATABASE_URL is not None
    value = create_async_engine(DATABASE_URL)
    await _truncate(value)
    try:
        yield value
    finally:
        await _truncate(value)
        await value.dispose()


@pytest.mark.asyncio
async def test_fake_llm_and_amap_replay_complete_mathematically_identical_chain(
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    repository = TaskRepository(engine)
    verified_fixture = write_deterministic_gis_fixture(tmp_path / "verified")
    await _create_task(repository)
    verified = await _run_chain(repository, verified_fixture, amap_mode="verified")

    await _truncate(engine)
    degraded_fixture = write_deterministic_gis_fixture(tmp_path / "degraded")
    await _create_task(repository)
    degraded = await _run_chain(repository, degraded_fixture, amap_mode="degraded")

    for task in (verified, degraded):
        assert task.task_id == TASK_ID
        assert task.status is TaskStatus.COMPLETED
        assert task.progress == 100
        assert task.plan is not None and task.plan.source is PlanSource.REAL_LLM
        assert tuple(step.status for step in task.steps) == (StepStatus.COMPLETED,) * 4
        assert {artifact.artifact_type for artifact in task.artifacts} == REQUIRED_TYPES
        assert task.analysis is not None
        assert task.quality is not None and task.quality.metrics.passed
        assert task.publication is not None

    assert verified.analysis is not None and degraded.analysis is not None
    assert verified.quality is not None and degraded.quality is not None
    assert verified.analysis.statistics == degraded.analysis.statistics
    assert verified.analysis.statistics.model_dump(exclude={"schema_version"}) == {
        "increase_hectares": 0.04,
        "stable_hectares": 0.08,
        "decrease_hectares": 0.04,
        "valid_hectares": 0.16,
    }
    assert _artifact_signatures(verified) == _artifact_signatures(degraded)
    assert verified.quality.metrics.model_dump(exclude={"elapsed_ms", "evidence"}) == (
        degraded.quality.metrics.model_dump(exclude={"elapsed_ms", "evidence"})
    )
    assert verified.quality.metrics.evidence[:3] == degraded.quality.metrics.evidence[:3]

    verified_report = _report_text(verified_fixture)
    degraded_report = _report_text(degraded_fixture)
    assert _normalize_observational_report_fields(verified_report) == (
        _normalize_observational_report_fields(degraded_report)
    )
    for expected in (
        str(TASK_ID),
        "2019-08-19",
        "2024-08-12",
        "增加 0.04 公顷",
        "稳定 0.08 公顷",
        "减少 0.04 公顷",
        "有效面积 0.16 公顷",
        "覆盖率 100.00%",
        "有效像元率 100.00%",
        "结论 PASS",
    ):
        assert expected in verified_report


@pytest.mark.asyncio
async def test_invalid_llm_plan_uses_labeled_recovery_and_persists_failure_evidence(
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    repository = TaskRepository(engine)
    fixture = write_deterministic_gis_fixture(tmp_path)
    await _create_task(repository)

    task, _events = await _execute_task(
        repository,
        fixture,
        worker_id="t25-invalid-llm",
        agent_transport=routed_agent_transport(),
        llm_transport=httpx.MockTransport(_fake_invalid_llm),
        amap_transport=httpx.MockTransport(_fake_verified_amap),
    )

    assert task.status is TaskStatus.COMPLETED
    assert task.plan is not None and task.plan.source is PlanSource.BUILTIN_RECOVERY
    assert task.publication is not None
    async with engine.connect() as connection:
        model_call = (
            (
                await connection.execute(
                    text("SELECT status, error_code FROM model_calls WHERE plan_id = :plan_id"),
                    {"plan_id": task.plan.plan_id},
                )
            )
            .mappings()
            .one()
        )
    assert dict(model_call) == {"status": "FAILED", "error_code": "LLM_PLAN_INVALID"}


@pytest.mark.asyncio
async def test_non_target_area_is_rejected_before_planning_or_agent_calls(
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    repository = TaskRepository(engine)
    fixture = write_deterministic_gis_fixture(tmp_path)
    await _create_task(repository, query="分析长江流域 2019 至 2024 年植被变化")

    task, events = await _execute_task(
        repository,
        fixture,
        worker_id="t25-out-of-scope",
        agent_transport=httpx.MockTransport(_unexpected_external_call),
        llm_transport=httpx.MockTransport(_unexpected_external_call),
        amap_transport=httpx.MockTransport(_unexpected_external_call),
    )

    assert task.status is TaskStatus.FAILED
    assert task.last_error is not None and task.last_error.code is ErrorCode.VALIDATION_ERROR
    assert task.plan is None
    assert task.artifacts == ()
    assert task.publication is None
    assert events[-1].step_id == "planning"
    assert "REJECTED/OUT_OF_SCOPE_STUDY_AREA" in events[-1].message


@pytest.mark.asyncio
async def test_corrupt_approved_data_fails_before_any_analysis_artifact(
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    repository = TaskRepository(engine)
    fixture = write_deterministic_gis_fixture(tmp_path)
    (fixture.cache_dir / "after_nir.tif").write_bytes(b"corrupt")
    await _create_task(repository)

    task, events = await _execute_task(
        repository,
        fixture,
        worker_id="t25-corrupt-data",
        agent_transport=routed_agent_transport(),
        llm_transport=httpx.MockTransport(_fake_llm),
        amap_transport=httpx.MockTransport(_fake_verified_amap),
    )

    _assert_failed_without_publication(task, ErrorCode.DATA_INVALID)
    assert task.artifacts == ()
    assert events[-1].step_id == "prepare_data"


@pytest.mark.asyncio
async def test_unreachable_agent_fails_without_claiming_success(
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    repository = TaskRepository(engine)
    fixture = write_deterministic_gis_fixture(tmp_path)
    await _create_task(repository)

    task, events = await _execute_task(
        repository,
        fixture,
        worker_id="t25-unreachable-agent",
        agent_transport=httpx.MockTransport(_unreachable_agent),
        llm_transport=httpx.MockTransport(_fake_llm),
        amap_transport=httpx.MockTransport(_fake_verified_amap),
    )

    _assert_failed_without_publication(task, ErrorCode.DEPENDENCY_UNAVAILABLE)
    assert task.artifacts == ()
    assert events[-1].step_id == "prepare_data"


@pytest.mark.asyncio
async def test_missing_analysis_artifact_fails_quality_and_never_publishes(
    engine: AsyncEngine,
    tmp_path: Path,
) -> None:
    repository = TaskRepository(engine)
    fixture = write_deterministic_gis_fixture(tmp_path)
    await _create_task(repository)

    task, events = await _execute_task(
        repository,
        fixture,
        worker_id="t25-partial-artifacts",
        agent_transport=_DeleteBeforeQualityTransport(fixture),
        llm_transport=httpx.MockTransport(_fake_llm),
        amap_transport=httpx.MockTransport(_fake_verified_amap),
    )

    _assert_failed_without_publication(task, ErrorCode.QUALITY_FAILED)
    artifact_types = {artifact.artifact_type for artifact in task.artifacts}
    assert ArtifactType.QUALITY_REPORT in artifact_types
    assert ArtifactType.PDF_REPORT not in artifact_types
    assert events[-1].step_id == "evaluate_quality"


async def _run_chain(
    repository: TaskRepository,
    fixture: DeterministicGisFixture,
    *,
    amap_mode: str,
) -> TaskResponse:
    task, events = await _execute_task(
        repository,
        fixture,
        worker_id=f"t25-{amap_mode}",
        agent_transport=routed_agent_transport(),
        llm_transport=httpx.MockTransport(_fake_llm),
        amap_transport=httpx.MockTransport(
            _fake_verified_amap if amap_mode == "verified" else _fake_degraded_amap
        ),
    )
    if amap_mode == "verified":
        assert events[0].message.startswith("在线位置校验通过（VERIFIED/ONLINE_MATCH_CONFIRMED）")
    else:
        assert events[0].message.startswith("在线位置校验已降级（DEGRADED/ONLINE_RATE_LIMITED）")
    return task


async def _execute_task(
    repository: TaskRepository,
    fixture: DeterministicGisFixture,
    *,
    worker_id: str,
    agent_transport: httpx.AsyncBaseTransport,
    llm_transport: httpx.AsyncBaseTransport,
    amap_transport: httpx.AsyncBaseTransport,
) -> tuple[TaskResponse, tuple[TaskEvent, ...]]:
    with configured_agent_apps(fixture):
        async with (
            httpx.AsyncClient(transport=agent_transport) as agent_http,
            httpx.AsyncClient(transport=llm_transport) as llm_http,
            httpx.AsyncClient(transport=amap_transport) as amap_http,
        ):
            planner = RecoveryTaskPlanner(
                LlmPlanningAdapter(
                    LlmConfig(
                        api_key="integration-only-llm-key",
                        base_url="https://llm.test/v1",
                        model="deterministic-planner",
                        timeout_seconds=1,
                    ),
                    llm_http,
                ),
                now=lambda: NOW,
            )
            grounder = StudyAreaGrounder(
                AmapStudyAreaVerifier(
                    AmapConfig(api_key="integration-only-amap-key", timeout_seconds=1),
                    amap_http,
                ),
                now=lambda: NOW,
            )
            orchestrator = TaskOrchestrator(
                repository,
                AgentHttpClient(
                    AgentClientConfig(
                        data_base_url="http://data.test",
                        analysis_base_url="http://analysis.test",
                        quality_base_url="http://quality.test",
                        publisher_base_url="http://publisher.test",
                    ),
                    agent_http,
                ),
                planner,
                study_area_grounder=grounder,
                now=lambda: NOW,
            )
            worker = OrchestrationWorker(
                repository,
                orchestrator,
                WorkerConfig(
                    worker_id=worker_id,
                    poll_interval_seconds=0.01,
                    lease_seconds=30,
                    heartbeat_interval_seconds=10,
                ),
                now=lambda: NOW,
            )
            assert await worker.run_once()

    task = await repository.get_task(TASK_ID)
    assert task is not None
    return task, await repository.list_events(TASK_ID)


async def _create_task(
    repository: TaskRepository,
    *,
    query: str = "分析神农溪 2019 至 2024 年植被变化",
) -> None:
    await repository.ensure_watershed(
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
        request=CreateTaskRequest(query=query),
        created_at=NOW,
    )


def _fake_llm(request: httpx.Request) -> httpx.Response:
    assert request.url == "https://llm.test/v1/chat/completions"
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"steps":['
                            '{"kind":"prepare_data","title":"准备批准数据"},'
                            '{"kind":"analyze_ndvi_change","title":"计算 NDVI 变化"},'
                            '{"kind":"evaluate_quality","title":"核验成果质量"},'
                            '{"kind":"publish_results","title":"发布地图与报告"}'
                            "]}"
                        )
                    }
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 40},
        },
    )


def _fake_invalid_llm(request: httpx.Request) -> httpx.Response:
    assert request.url == "https://llm.test/v1/chat/completions"
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"steps":['
                            '{"kind":"prepare_data","title":"准备批准数据"},'
                            '{"kind":"publish_results","title":"越过质量门禁"}'
                            "]}"
                        )
                    }
                }
            ]
        },
    )


def _fake_verified_amap(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "1",
            "info": "OK",
            "infocode": "10000",
            "pois": [{"name": "神农溪景区", "adcode": "422823", "typecode": "110000"}],
        },
    )


def _fake_degraded_amap(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, json={"status": "0", "info": "RATE_LIMIT", "infocode": "10004"})


def _unexpected_external_call(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected external call to {request.url.host}")


def _unreachable_agent(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("private integration transport failure", request=request)


class _DeleteBeforeQualityTransport(httpx.AsyncBaseTransport):
    def __init__(self, fixture: DeterministicGisFixture) -> None:
        self._fixture = fixture
        self._delegate = routed_agent_transport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/v1/quality/evaluate":
            statistics = (
                self._fixture.artifact_root
                / str(TASK_ID)
                / "attempt-1"
                / "analysis"
                / "area_statistics.json"
            )
            statistics.unlink()
        return await self._delegate.handle_async_request(request)

    async def aclose(self) -> None:
        await self._delegate.aclose()


def _assert_failed_without_publication(task: TaskResponse, code: ErrorCode) -> None:
    assert task.status is TaskStatus.FAILED
    assert task.last_error is not None and task.last_error.code is code
    assert task.publication is None


def _artifact_signatures(task: TaskResponse) -> dict[ArtifactType, tuple[str | None, int | None]]:
    return {
        artifact.artifact_type: (artifact.checksum_sha256, artifact.byte_size)
        for artifact in task.artifacts
        if artifact.artifact_type in ANALYSIS_TYPES
    }


def _report_text(fixture: DeterministicGisFixture) -> str:
    report_path = fixture.artifact_root / str(TASK_ID) / "attempt-1" / "publisher" / "report.pdf"
    return "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(report_path.read_bytes())).pages
    )


def _normalize_observational_report_fields(value: str) -> str:
    value = re.sub(r"Analysis 耗时 \d+ ms", "Analysis 耗时 <实测> ms", value)
    value = re.sub(r"耗时 \d+ 毫秒", "耗时 <实测> 毫秒", value)
    value = re.sub(
        r"(质量评价报告\s+)\d+\s+[0-9a-f]{64}",
        r"\1<实测字节>\n<实测校验和>",
        value,
    )
    return re.sub(
        r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00",
        "<生成时间>",
        value,
    )


async def _truncate(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE watersheds RESTART IDENTITY CASCADE"))
