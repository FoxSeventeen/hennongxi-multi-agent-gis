"""Independently startable Master Agent application."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from hennongxi_contracts import AgentName
from hennongxi_observability import create_observed_agent_app

from hennongxi_master.health import install_master_health_routes
from hennongxi_master.repository import TaskRepository
from hennongxi_master.runtime import MasterWorkerRuntime, create_worker_runtime
from hennongxi_master.tasks import install_master_task_routes
from hennongxi_master.worker import WorkerConfig

PORT = 8000
DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://hennongxi:local-development-only@postgis:5432/hennongxi"
)


@asynccontextmanager
async def _repository_lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.task_repository = None
    runtime: MasterWorkerRuntime | None = None
    worker_task: asyncio.Task[None] | None = None
    stop_worker: asyncio.Event | None = None
    try:
        config: WorkerConfig = app.state.worker_config
        if config.enabled:
            repository = app.state.task_repository_factory()
            app.state.task_repository = repository
            runtime = app.state.worker_runtime_factory(repository, config)
            stop_worker = asyncio.Event()
            worker_task = asyncio.create_task(
                runtime.worker.serve(stop_worker),
                name="master-orchestration-worker",
            )
        yield
    finally:
        try:
            if worker_task is not None and stop_worker is not None:
                stop_worker.set()
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            if runtime is not None:
                await runtime.close()
        finally:
            repository = app.state.task_repository
            if isinstance(repository, TaskRepository):
                await repository.dispose()
            app.state.task_repository = None


def create_master_app(environment: Mapping[str, str] | None = None) -> FastAPI:
    values = os.environ if environment is None else environment
    master = create_observed_agent_app(
        AgentName.MASTER,
        PORT,
        resource_lifespan=_repository_lifespan,
    )
    master.state.task_repository_factory = lambda: TaskRepository.from_url(
        values.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    master.state.worker_config = WorkerConfig.from_environment(values)
    master.state.worker_runtime_factory = lambda repository, config: create_worker_runtime(
        repository,
        config,
        values,
    )
    install_master_health_routes(master)
    install_master_task_routes(master)
    return master


app = create_master_app()
