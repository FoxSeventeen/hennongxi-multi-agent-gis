"""Production planning fallback and single-claim orchestration worker."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, Self
from uuid import UUID, uuid4

import structlog
from hennongxi_contracts import ExecutionPlan, ModelCallRecord, TaskResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hennongxi_master.llm import LlmPlanningError
from hennongxi_master.orchestrator import PlanningOutcome
from hennongxi_master.planning import build_builtin_recovery_plan
from hennongxi_master.repository import (
    InterruptedRecoveryResult,
    RepositoryConflict,
    WorkerClaim,
    WorkerClaimRequest,
    WorkerLeaseRenewal,
)

_LOGGER = structlog.get_logger("hennongxi.master.worker")


class WorkerConfig(BaseModel):
    """Non-secret worker controls loaded from the process environment."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
    )

    enabled: bool = False
    worker_id: str = Field(default="master-agent-1", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    poll_interval_seconds: float = Field(default=0.5, ge=0.01, le=30)
    lease_seconds: int = Field(default=120, ge=2, le=3600)
    heartbeat_interval_seconds: float = Field(default=30, ge=0.01, le=1200)

    @model_validator(mode="after")
    def heartbeat_must_precede_expiry(self) -> Self:
        if self.heartbeat_interval_seconds >= self.lease_seconds:
            raise ValueError("worker heartbeat interval must be shorter than its lease")
        return self

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Self:
        values = os.environ if environment is None else environment
        return cls.model_validate(
            {
                "enabled": values.get("ORCHESTRATION_WORKER_ENABLED", "false"),
                "worker_id": values.get("ORCHESTRATION_WORKER_ID", "master-agent-1"),
                "poll_interval_seconds": values.get("ORCHESTRATION_POLL_SECONDS", "0.5"),
                "lease_seconds": values.get("ORCHESTRATION_LEASE_SECONDS", "120"),
                "heartbeat_interval_seconds": values.get("ORCHESTRATION_HEARTBEAT_SECONDS", "30"),
            }
        )


class LlmPlanAdapter(Protocol):
    async def create_plan(self, *, task_id: UUID, query: str) -> ExecutionPlan: ...


class WorkerRepository(Protocol):
    async def claim_next_task(self, value: WorkerClaimRequest) -> WorkerClaim | None: ...

    async def renew_claim(
        self,
        claim: WorkerClaim,
        value: WorkerLeaseRenewal,
    ) -> WorkerClaim: ...

    async def recover_interrupted_attempt(
        self,
        claim: WorkerClaim,
        *,
        recovered_at: datetime,
    ) -> InterruptedRecoveryResult | None: ...

    async def release_claim(
        self,
        claim: WorkerClaim,
        *,
        released_at: datetime,
    ) -> WorkerClaim: ...


class TaskRunner(Protocol):
    async def run(self, task_id: UUID, *, attempt: int) -> TaskResponse: ...


class WorkerLeaseLost(RuntimeError):
    """Raised without repository details when another worker owns the lease."""

    def __init__(self) -> None:
        super().__init__("worker lease was lost")


class RecoveryTaskPlanner:
    """Use the configured LLM or a visibly labeled deterministic recovery plan."""

    def __init__(
        self,
        adapter: LlmPlanAdapter | None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._adapter = adapter
        self._now = now or _utc_now

    async def create_plan(self, task: TaskResponse) -> PlanningOutcome:
        identity = {
            "task_id": str(task.task_id),
            "attempt": task.current_attempt,
            "correlation_id": str(task.correlation_id),
        }
        if self._adapter is None:
            _LOGGER.warning(
                "planning_recovery_selected",
                **identity,
                reason_code="LLM_NOT_CONFIGURED",
            )
            return self._recovery(task)

        try:
            return PlanningOutcome(
                plan=await self._adapter.create_plan(task_id=task.task_id, query=task.query)
            )
        except LlmPlanningError as error:
            _LOGGER.warning(
                "planning_recovery_selected",
                **identity,
                reason_code=error.code,
                retryable=error.retryable,
            )
            return self._recovery(task, failed_model_call=error.model_call)

    def _recovery(
        self,
        task: TaskResponse,
        *,
        failed_model_call: ModelCallRecord | None = None,
    ) -> PlanningOutcome:
        return PlanningOutcome(
            plan=build_builtin_recovery_plan(
                task_id=task.task_id,
                plan_id=uuid4(),
                created_at=self._now(),
            ),
            failed_model_call=failed_model_call,
        )


class OrchestrationWorker:
    """Claim and run at most one durable task at a time while heartbeating its lease."""

    def __init__(
        self,
        repository: WorkerRepository,
        runner: TaskRunner,
        config: WorkerConfig,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._config = config
        self._now = now or _utc_now

    async def run_once(self) -> bool:
        claim = await self._repository.claim_next_task(
            WorkerClaimRequest(
                worker_id=self._config.worker_id,
                claimed_at=self._now(),
                lease_seconds=self._config.lease_seconds,
            )
        )
        if claim is None:
            return False

        fields = {
            "task_id": str(claim.task_id),
            "attempt": claim.attempt,
            "worker_id": claim.worker_id,
        }
        _LOGGER.info("worker_claim_acquired", **fields)
        try:
            recovery = await self._repository.recover_interrupted_attempt(
                claim,
                recovered_at=self._now(),
            )
            if recovery is not None:
                _LOGGER.warning(
                    "interrupted_attempt_requeued",
                    **fields,
                    retry_attempt=recovery.retry_attempt,
                    resume_from_step_id=recovery.resume_from_step_id,
                )
                return True
            await self._run_claim(claim)
        finally:
            try:
                await self._repository.release_claim(claim, released_at=self._now())
                _LOGGER.info("worker_claim_released", **fields)
            except RepositoryConflict:
                _LOGGER.warning("worker_claim_release_skipped", **fields, reason_code="LEASE_STALE")
        return True

    async def serve(self, stop: asyncio.Event) -> None:
        _LOGGER.info("orchestration_worker_started", worker_id=self._config.worker_id)
        while not stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                _LOGGER.error(
                    "orchestration_worker_iteration_failed",
                    worker_id=self._config.worker_id,
                    error_type=type(error).__name__,
                )
            await _wait_for_stop(stop, self._config.poll_interval_seconds)
        _LOGGER.info("orchestration_worker_stopped", worker_id=self._config.worker_id)

    async def _run_claim(self, claim: WorkerClaim) -> None:
        stop_heartbeat = asyncio.Event()
        execution = asyncio.create_task(
            self._runner.run(claim.task_id, attempt=claim.attempt),
            name=f"orchestrate-{claim.task_id}",
        )
        heartbeat = asyncio.create_task(
            self._heartbeat(claim, stop_heartbeat),
            name=f"heartbeat-{claim.task_id}",
        )
        try:
            await asyncio.wait({execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat.done():
                try:
                    await heartbeat
                except RepositoryConflict:
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    raise WorkerLeaseLost() from None
                if not execution.done():
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                    raise WorkerLeaseLost()
            await execution
        finally:
            stop_heartbeat.set()
            if not heartbeat.done():
                await heartbeat
            if not execution.done():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)

    async def _heartbeat(self, claim: WorkerClaim, stop: asyncio.Event) -> None:
        while not await _wait_for_stop(stop, self._config.heartbeat_interval_seconds):
            renewal = WorkerLeaseRenewal(
                heartbeat_at=self._now(),
                lease_seconds=self._config.lease_seconds,
            )
            await self._repository.renew_claim(claim, renewal)
            _LOGGER.debug(
                "worker_claim_renewed",
                task_id=str(claim.task_id),
                attempt=claim.attempt,
                worker_id=claim.worker_id,
            )


async def _wait_for_stop(stop: asyncio.Event, timeout_seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=timeout_seconds)
    except TimeoutError:
        return False
    return True


def _utc_now() -> datetime:
    return datetime.now(UTC)
