from __future__ import annotations

import asyncio
from collections.abc import Mapping
from threading import Event
from typing import Any

from fastapi.testclient import TestClient
from hennongxi_master.main import create_master_app


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
