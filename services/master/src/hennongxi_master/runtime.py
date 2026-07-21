"""Compose lifespan-scoped clients and the production orchestration worker."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
import structlog

from hennongxi_master.agent_client import (
    AgentClientConfig,
    AgentHttpClient,
    create_agent_async_client,
)
from hennongxi_master.llm import (
    LlmConfig,
    LlmConfigurationError,
    LlmPlanningAdapter,
)
from hennongxi_master.orchestrator import EventPublisher, TaskOrchestrator
from hennongxi_master.repository import TaskRepository
from hennongxi_master.worker import OrchestrationWorker, RecoveryTaskPlanner, WorkerConfig

_LOGGER = structlog.get_logger("hennongxi.master.runtime")


@dataclass(frozen=True, slots=True)
class MasterWorkerRuntime:
    worker: OrchestrationWorker
    http_clients: tuple[httpx.AsyncClient, ...]

    async def close(self) -> None:
        await asyncio.gather(*(client.aclose() for client in self.http_clients))


def create_worker_runtime(
    repository: TaskRepository,
    config: WorkerConfig,
    environment: Mapping[str, str] | None = None,
    event_publisher: EventPublisher | None = None,
) -> MasterWorkerRuntime:
    """Build one worker with shared, bounded HTTP connection pools."""

    agent_config = AgentClientConfig.from_environment(environment)
    agent_http_client = create_agent_async_client(agent_config)
    http_clients = [agent_http_client]

    try:
        llm_config = LlmConfig.from_environment(environment)
    except LlmConfigurationError:
        llm_adapter = None
        _LOGGER.warning("llm_planner_unconfigured", reason_code="LLM_NOT_CONFIGURED")
    else:
        llm_http_client = _create_llm_async_client(llm_config)
        http_clients.append(llm_http_client)
        llm_adapter = LlmPlanningAdapter(llm_config, llm_http_client)

    planner = RecoveryTaskPlanner(llm_adapter)
    orchestrator = TaskOrchestrator(
        repository,
        AgentHttpClient(agent_config, agent_http_client),
        planner,
        event_publisher,
    )
    return MasterWorkerRuntime(
        worker=OrchestrationWorker(repository, orchestrator, config),
        http_clients=tuple(http_clients),
    )


def _create_llm_async_client(config: LlmConfig) -> httpx.AsyncClient:
    # Sources: https://www.python-httpx.org/async/#opening-and-closing-clients
    # and https://www.python-httpx.org/advanced/clients/#why-use-a-client
    return httpx.AsyncClient(
        timeout=httpx.Timeout(config.timeout_seconds),
        limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        follow_redirects=False,
        trust_env=False,
    )
