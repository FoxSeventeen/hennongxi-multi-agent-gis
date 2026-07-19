"""Runtime dependency probes and safe Master readiness responses."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import asyncpg  # type: ignore[import-untyped]
import httpx
from fastapi import FastAPI, Request
from hennongxi_contracts import (
    HealthResponse,
    HealthState,
    ReadinessBlocker,
    ReadinessResponse,
    ServiceHealth,
    ServiceName,
)
from redis.asyncio import Redis

AGENT_ENVIRONMENT_KEYS: tuple[tuple[ServiceName, str, str], ...] = (
    (ServiceName.DATA, "DATA_AGENT_BASE_URL", "http://data-agent:8001"),
    (ServiceName.ANALYSIS, "ANALYSIS_AGENT_BASE_URL", "http://analysis-agent:8002"),
    (ServiceName.QUALITY, "QUALITY_AGENT_BASE_URL", "http://quality-agent:8003"),
    (ServiceName.PUBLISHER, "PUBLISHER_AGENT_BASE_URL", "http://publisher-agent:8004"),
)
DEPENDENCY_NAMES = tuple(item[0] for item in AGENT_ENVIRONMENT_KEYS) + (
    ServiceName.POSTGIS,
    ServiceName.REDIS,
)


class DependencyProbe(Protocol):
    async def check_dependencies(self) -> tuple[ServiceHealth, ...]:
        """Return one health result for every required runtime dependency."""


@dataclass(frozen=True)
class RuntimeDependencyProbe:
    """Probe Agents over HTTP and stateful services through their native protocols."""

    agent_endpoints: tuple[tuple[ServiceName, str], ...]
    database_url: str
    redis_url: str
    timeout_seconds: float = 2.0

    @classmethod
    def from_environment(cls) -> RuntimeDependencyProbe:
        return cls(
            agent_endpoints=tuple(
                (service, os.getenv(key, default))
                for service, key, default in AGENT_ENVIRONMENT_KEYS
            ),
            database_url=_asyncpg_url(
                os.getenv(
                    "DATABASE_URL",
                    "postgresql+asyncpg://hennongxi:local-development-only@postgis:5432/hennongxi",
                )
            ),
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        )

    async def check_dependencies(self) -> tuple[ServiceHealth, ...]:
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            results = await asyncio.gather(
                *(
                    self._check_agent(client, service, endpoint)
                    for service, endpoint in self.agent_endpoints
                ),
                self._check_postgis(),
                self._check_redis(),
            )
        return tuple(results)

    async def _check_agent(
        self,
        client: httpx.AsyncClient,
        service: ServiceName,
        endpoint: str,
    ) -> ServiceHealth:
        try:
            response = await client.get(f"{endpoint.rstrip('/')}/internal/v1/health")
            response.raise_for_status()
            health = ServiceHealth.model_validate(response.json())
            if health.service != service:
                raise ValueError("health response service mismatch")
            return health
        except Exception:
            return _unavailable(service)

    async def _check_postgis(self) -> ServiceHealth:
        connection: asyncpg.Connection[asyncpg.Record] | None = None
        try:
            connection = await asyncpg.connect(
                self.database_url,
                timeout=self.timeout_seconds,
            )
            version = await connection.fetchval("SELECT PostGIS_Version()")
            if not version:
                raise RuntimeError("PostGIS extension is unavailable")
            return _healthy(ServiceName.POSTGIS)
        except Exception:
            return _unavailable(ServiceName.POSTGIS)
        finally:
            if connection is not None:
                await connection.close()

    async def _check_redis(self) -> ServiceHealth:
        client: Redis | None = None
        try:
            client = Redis.from_url(
                self.redis_url,
                socket_connect_timeout=self.timeout_seconds,
                socket_timeout=self.timeout_seconds,
            )
            if not await client.ping():
                raise RuntimeError("Redis ping failed")
            return _healthy(ServiceName.REDIS)
        except Exception:
            return _unavailable(ServiceName.REDIS)
        finally:
            if client is not None:
                await client.aclose()


def install_master_health_routes(app: FastAPI) -> None:
    """Install public aggregate health routes without weakening local liveness."""

    app.state.dependency_probe = RuntimeDependencyProbe.from_environment()

    @app.get("/api/v1/health", response_model=HealthResponse)
    async def get_aggregate_health(request: Request) -> HealthResponse:
        dependencies = await _probe_dependencies(request.app.state.dependency_probe)
        services = (_healthy(ServiceName.MASTER), *dependencies)
        return HealthResponse(
            state=_aggregate_state(services),
            checked_at=datetime.now(UTC),
            services=services,
        )

    @app.get("/api/v1/config/readiness", response_model=ReadinessResponse)
    async def get_configuration_readiness(request: Request) -> ReadinessResponse:
        dependencies = await _probe_dependencies(request.app.state.dependency_probe)
        llm_configured = all(
            _configured(os.getenv(key)) for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
        )
        data_configured = _manifest_is_available(os.getenv("DATA_MANIFEST_PATH"))
        dependencies_available = all(
            service.state is HealthState.HEALTHY for service in dependencies
        )

        blockers: list[ReadinessBlocker] = []
        messages: list[str] = []
        if not llm_configured:
            blockers.append(ReadinessBlocker.LLM_NOT_CONFIGURED)
            messages.append("LLM configuration is incomplete")
        if not data_configured:
            blockers.append(ReadinessBlocker.DATA_NOT_CONFIGURED)
            messages.append("data manifest is unavailable")
        if not dependencies_available:
            blockers.append(ReadinessBlocker.DEPENDENCY_UNAVAILABLE)
            messages.append("one or more runtime dependencies are unavailable")

        return ReadinessResponse(
            ready=not blockers,
            llm_configured=llm_configured,
            data_configured=data_configured,
            blockers=tuple(blockers),
            messages=tuple(messages),
        )


async def _probe_dependencies(probe: DependencyProbe) -> tuple[ServiceHealth, ...]:
    try:
        services = await probe.check_dependencies()
    except Exception:
        return tuple(_unavailable(service) for service in DEPENDENCY_NAMES)

    by_name = {service.service: service for service in services}
    return tuple(by_name.get(service, _unavailable(service)) for service in DEPENDENCY_NAMES)


def _aggregate_state(services: Sequence[ServiceHealth]) -> HealthState:
    if any(service.state is HealthState.UNAVAILABLE for service in services):
        return HealthState.UNAVAILABLE
    if any(service.state is HealthState.DEGRADED for service in services):
        return HealthState.DEGRADED
    return HealthState.HEALTHY


def _healthy(service: ServiceName) -> ServiceHealth:
    return ServiceHealth(
        service=service,
        state=HealthState.HEALTHY,
        checked_at=datetime.now(UTC),
    )


def _unavailable(service: ServiceName) -> ServiceHealth:
    return ServiceHealth(
        service=service,
        state=HealthState.UNAVAILABLE,
        checked_at=datetime.now(UTC),
        message="health check failed",
    )


def _asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())


def _manifest_is_available(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    try:
        return Path(value).is_file()
    except OSError:
        return False
