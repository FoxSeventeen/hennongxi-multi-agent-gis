"""Independently startable Master Agent application."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from hennongxi_contracts import AgentName
from hennongxi_observability import create_observed_agent_app

from hennongxi_master.health import install_master_health_routes
from hennongxi_master.repository import TaskRepository
from hennongxi_master.tasks import install_master_task_routes

PORT = 8000
DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://hennongxi:local-development-only@postgis:5432/hennongxi"
)


@asynccontextmanager
async def _repository_lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.task_repository = None
    try:
        yield
    finally:
        repository = app.state.task_repository
        if isinstance(repository, TaskRepository):
            await repository.dispose()


def create_master_app() -> FastAPI:
    master = create_observed_agent_app(
        AgentName.MASTER,
        PORT,
        resource_lifespan=_repository_lifespan,
    )
    master.state.task_repository_factory = lambda: TaskRepository.from_url(
        os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    )
    install_master_health_routes(master)
    install_master_task_routes(master)
    return master


app = create_master_app()
