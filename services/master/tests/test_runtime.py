from __future__ import annotations

import asyncio
from collections.abc import Mapping
from threading import Event
from typing import Any, cast

from fastapi.testclient import TestClient
from hennongxi_master.amap import AmapStudyAreaVerifier
from hennongxi_master.main import create_master_app
from hennongxi_master.orchestrator import TaskOrchestrator
from hennongxi_master.repository import TaskRepository
from hennongxi_master.runtime import create_worker_runtime
from hennongxi_master.study_area import StudyAreaGrounder
from hennongxi_master.worker import WorkerConfig


class _Repository:
    pass


class _Worker:
    def __init__(self) -> None:
        self.started = Event()
        self.stopped = Event()

    async def serve(self, stop: asyncio.Event) -> None:
        self.started.set()
        try:
            await stop.wait()
        finally:
            self.stopped.set()


class _Runtime:
    def __init__(self) -> None:
        self.worker = _Worker()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _environment(enabled: bool) -> Mapping[str, str]:
    return {
        "ORCHESTRATION_WORKER_ENABLED": str(enabled).lower(),
        "ORCHESTRATION_WORKER_ID": "master-test-1",
        "ORCHESTRATION_POLL_SECONDS": "0.01",
        "ORCHESTRATION_LEASE_SECONDS": "30",
        "ORCHESTRATION_HEARTBEAT_SECONDS": "10",
    }


def test_disabled_worker_keeps_repository_lazy() -> None:
    master = create_master_app(environment=_environment(False))
    factory_calls: list[Any] = []
    master.state.worker_runtime_factory = lambda *values: factory_calls.append(values)

    with TestClient(master):
        assert master.state.task_repository is None

    assert factory_calls == []


def test_enabled_worker_starts_and_closes_lifespan_runtime() -> None:
    repository = _Repository()
    runtime = _Runtime()
    master = create_master_app(environment=_environment(True))
    master.state.task_repository_factory = lambda: repository
    master.state.worker_runtime_factory = lambda *_values: runtime

    with TestClient(master):
        assert runtime.worker.started.wait(timeout=1)
        assert master.state.task_repository is repository

    assert runtime.worker.stopped.wait(timeout=1)
    assert runtime.closed is True


def test_worker_runtime_keeps_online_grounding_optional_when_unconfigured() -> None:
    runtime = create_worker_runtime(
        cast(TaskRepository, _Repository()),
        WorkerConfig.from_environment(_environment(True)),
        _environment(True),
    )
    runner = cast(TaskOrchestrator, runtime.worker._runner)
    grounder = cast(StudyAreaGrounder, runner._study_area_grounder)

    assert grounder.online_verifier is None
    assert len(runtime.http_clients) == 1

    asyncio.run(runtime.close())


def test_worker_runtime_injects_bounded_amap_verifier_only_into_master() -> None:
    environment = {
        **_environment(True),
        "AMAP_WEB_SERVICE_KEY": "test-amap-runtime-key",
        "AMAP_TIMEOUT_SECONDS": "2.5",
    }

    runtime = create_worker_runtime(
        cast(TaskRepository, _Repository()),
        WorkerConfig.from_environment(environment),
        environment,
    )
    runner = cast(TaskOrchestrator, runtime.worker._runner)
    grounder = cast(StudyAreaGrounder, runner._study_area_grounder)

    assert isinstance(grounder.online_verifier, AmapStudyAreaVerifier)
    assert len(runtime.http_clients) == 2
    assert "test-amap-runtime-key" not in repr(grounder)
    assert "test-amap-runtime-key" not in repr(runtime)

    asyncio.run(runtime.close())


def test_worker_runtime_accepts_an_explicit_test_grounder_without_amap_network() -> None:
    environment = {
        **_environment(True),
        "AMAP_WEB_SERVICE_KEY": "must-not-create-an-amap-client",
    }
    injected_grounder = StudyAreaGrounder(None)

    runtime = create_worker_runtime(
        cast(TaskRepository, _Repository()),
        WorkerConfig.from_environment(environment),
        environment,
        study_area_grounder=injected_grounder,
    )
    runner = cast(TaskOrchestrator, runtime.worker._runner)

    assert runner._study_area_grounder is injected_grounder
    assert len(runtime.http_clients) == 1
    assert "must-not-create-an-amap-client" not in repr(runtime)

    asyncio.run(runtime.close())
