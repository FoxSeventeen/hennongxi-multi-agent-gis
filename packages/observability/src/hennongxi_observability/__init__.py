"""Correlation-aware logging primitives used by every Agent service."""

from hennongxi_observability.correlation import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    correlation_headers,
    current_correlation_id,
)
from hennongxi_observability.fastapi import create_observed_agent_app
from hennongxi_observability.logging import configure_logging

__all__ = [
    "CORRELATION_ID_HEADER",
    "CorrelationIdMiddleware",
    "configure_logging",
    "correlation_headers",
    "create_observed_agent_app",
    "current_correlation_id",
]
