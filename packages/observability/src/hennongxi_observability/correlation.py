"""Correlation identity propagation for inbound and outbound HTTP."""

from __future__ import annotations

from contextvars import ContextVar, Token
from time import perf_counter
from uuid import UUID, uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_ID_HEADER = "X-Correlation-ID"
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def correlation_headers() -> dict[str, str]:
    correlation_id = current_correlation_id()
    if correlation_id is None:
        raise RuntimeError("correlation_headers() requires an active request context")
    return {CORRELATION_ID_HEADER: correlation_id}


def _canonical_correlation_id(raw_value: str | None) -> str:
    if raw_value is not None:
        try:
            return str(UUID(raw_value))
        except ValueError:
            pass
    return str(uuid4())


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Canonicalize one request identity and expose it to logs and HTTPX calls."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = _canonical_correlation_id(request.headers.get(CORRELATION_ID_HEADER))
        token: Token[str | None] = _correlation_id.set(correlation_id)
        request.state.correlation_id = correlation_id
        started = perf_counter()
        logger = structlog.get_logger("hennongxi.http")
        logger.info("request_started", method=request.method, path=request.url.path)

        try:
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = correlation_id
            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                elapsed_ms=round((perf_counter() - started) * 1_000),
            )
            return response
        except Exception:
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                elapsed_ms=round((perf_counter() - started) * 1_000),
            )
            raise
        finally:
            _correlation_id.reset(token)
