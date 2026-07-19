"""A minimal observed FastAPI shell shared without coupling Agent behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI
from hennongxi_contracts import AgentName, HealthState, ServiceHealth, ServiceName

from hennongxi_observability.correlation import CorrelationIdMiddleware
from hennongxi_observability.logging import configure_logging


def create_observed_agent_app(service: AgentName, port: int) -> FastAPI:
    """Create an independently startable Agent app with only local health."""

    configure_logging()
    logger = structlog.get_logger(f"hennongxi.{service.value}")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started = True
        logger.info("service_started", service=service.value, port=port)
        try:
            yield
        finally:
            app.state.started = False
            logger.info("service_stopped", service=service.value, port=port)

    app = FastAPI(
        title=f"Hennongxi {service.value.title()} Agent",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.service_name = service.value
    app.state.port = port
    app.state.started = False
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/internal/v1/health", response_model=ServiceHealth)
    def get_local_health() -> ServiceHealth:
        return ServiceHealth(
            service=ServiceName(service.value),
            state=HealthState.HEALTHY,
            checked_at=datetime.now(UTC),
        )

    return app
