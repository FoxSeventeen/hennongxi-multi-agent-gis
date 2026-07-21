from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hennongxi_contracts import (
    ExecutionPlan,
    ModelCallRecord,
    ModelCallStatus,
    PlanSource,
    TaskResponse,
    TaskStatus,
)
from hennongxi_master.llm import LlmPlanningError
from hennongxi_master.planning import build_builtin_recovery_plan
from hennongxi_master.repository import (
    RepositoryConflict,
    WorkerClaim,
    WorkerClaimRequest,
    WorkerLeaseRenewal,
)
from hennongxi_master.worker import (
    OrchestrationWorker,
    RecoveryTaskPlanner,
    WorkerConfig,
)

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
PLAN_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)


def _task() -> TaskResponse:
    return TaskResponse(
        task_id=TASK_ID,
        query="分析神农溪植被变化",
        status=TaskStatus.PENDING,
        progress=0,
        current_attempt=1,
        correlation_id=CORRELATION_ID,
        created_at=NOW,
        updated_at=NOW,
    )


def _plan() -> ExecutionPlan:
    return build_builtin_recovery_plan(task_id=TASK_ID, plan_id=PLAN_ID, created_at=NOW)


class _SuccessfulAdapter:
    async def create_plan(self, *, task_id: UUID, query: str) -> ExecutionPlan:
        assert task_id == TASK_ID
        assert query == "分析神农溪植被变化"
        return _plan().model_copy(update={"source": PlanSource.REAL_LLM})


class _FailingAdapter:
    def __init__(self) -> None:
        self.model_call = ModelCallRecord(
            model="configured-model",
            started_at=NOW,
            duration_ms=125,
            status=ModelCallStatus.FAILED,
            error_code="LLM_TIMEOUT",
        )

    async def create_plan(self, *, task_id: UUID, query: str) -> ExecutionPlan:
        del task_id, query
        raise LlmPlanningError(
            code="LLM_TIMEOUT",
            retryable=True,
            model_call=self.model_call,
        )


@pytest.mark.asyncio
async def test_planner_uses_real_plan_when_adapter_succeeds() -> None:
    result = await RecoveryTaskPlanner(_SuccessfulAdapter(), now=lambda: NOW).create_plan(_task())

    assert result.plan.source is PlanSource.REAL_LLM
    assert result.failed_model_call is None


@pytest.mark.asyncio
async def test_planner_labels_unconfigured_fallback_as_builtin_recovery() -> None:
    result = await RecoveryTaskPlanner(None, now=lambda: NOW).create_plan(_task())

    assert result.plan.source is PlanSource.BUILTIN_RECOVERY
    assert result.plan.task_id == TASK_ID
    assert result.failed_model_call is None


@pytest.mark.asyncio
async def test_planner_preserves_sanitized_failed_call_before_recovery() -> None:
    adapter = _FailingAdapter()

    result = await RecoveryTaskPlanner(adapter, now=lambda: NOW).create_plan(_task())

    assert result.plan.source is PlanSource.BUILTIN_RECOVERY
    assert result.failed_model_call == adapter.model_call


class _ClaimRepository:
    def __init__(self, *, claim: WorkerClaim | None = None, lose_lease: bool = False) -> None:
        self.claim = claim
        self.lose_lease = lose_lease
        self.requests: list[WorkerClaimRequest] = []
        self.renewals: list[WorkerLeaseRenewal] = []
        self.releases: list[tuple[WorkerClaim, datetime]] = []
        self.renewed = asyncio.Event()

    async def claim_next_task(self, value: WorkerClaimRequest) -> WorkerClaim | None:
        self.requests.append(value)
        return self.claim

    async def renew_claim(
        self,
        claim: WorkerClaim,
        value: WorkerLeaseRenewal,
    ) -> WorkerClaim:
        assert claim == self.claim
        self.renewals.append(value)
        self.renewed.set()
        if self.lose_lease:
            raise RepositoryConflict("private stale-lease detail")
        return claim.model_copy(
            update={
                "heartbeat_at": value.heartbeat_at,
                "lease_expires_at": value.heartbeat_at + timedelta(seconds=value.lease_seconds),
            }
        )

    async def release_claim(self, claim: WorkerClaim, *, released_at: datetime) -> WorkerClaim:
        self.releases.append((claim, released_at))
        if self.lose_lease:
            raise RepositoryConflict("private replacement-lease detail")
        return claim.model_copy(update={"released_at": released_at})


class _Runner:
    def __init__(self, repository: _ClaimRepository) -> None:
        self.repository = repository
        self.calls: list[tuple[UUID, int]] = []
        self.cancelled = False

    async def run(self, task_id: UUID, *, attempt: int) -> TaskResponse:
        self.calls.append((task_id, attempt))
        try:
            await self.repository.renewed.wait()
            if self.repository.lose_lease:
                await asyncio.Future[None]()
            return _task().model_copy(update={"status": TaskStatus.COMPLETED, "progress": 100})
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def _claim() -> WorkerClaim:
    return WorkerClaim(
        task_id=TASK_ID,
        attempt=1,
        worker_id="master-test-1",
        claimed_at=NOW,
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(seconds=30),
    )


def _config() -> WorkerConfig:
    return WorkerConfig(
        worker_id="master-test-1",
        poll_interval_seconds=0.01,
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )


@pytest.mark.asyncio
async def test_worker_reports_idle_without_invoking_orchestrator() -> None:
    repository = _ClaimRepository()
    runner = _Runner(repository)

    worked = await OrchestrationWorker(repository, runner, _config(), now=lambda: NOW).run_once()

    assert worked is False
    assert runner.calls == []
    assert repository.releases == []


@pytest.mark.asyncio
async def test_worker_renews_and_releases_exact_claim_after_completion() -> None:
    claim = _claim()
    repository = _ClaimRepository(claim=claim)
    runner = _Runner(repository)

    worked = await OrchestrationWorker(repository, runner, _config(), now=lambda: NOW).run_once()

    assert worked is True
    assert runner.calls == [(TASK_ID, 1)]
    assert len(repository.renewals) == 1
    assert repository.releases == [(claim, NOW)]


@pytest.mark.asyncio
async def test_worker_cancels_execution_when_lease_is_lost_and_sanitizes_error() -> None:
    repository = _ClaimRepository(claim=_claim(), lose_lease=True)
    runner = _Runner(repository)

    with pytest.raises(RuntimeError, match="worker lease was lost") as captured:
        await OrchestrationWorker(repository, runner, _config(), now=lambda: NOW).run_once()

    assert "private stale-lease detail" not in str(captured.value)
    assert runner.cancelled is True
    assert len(repository.releases) == 1
