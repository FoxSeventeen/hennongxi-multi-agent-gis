"""Private deterministic upstreams used only by the Compose E2E profile."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from hennongxi_contracts import (  # type: ignore[import-untyped]
    ErrorCode,
    ErrorResponse,
    PublisherPublishCommand,
    StructuredError,
)
from pydantic import BaseModel, ConfigDict, Field

_EXPECTED_AUTHORIZATION = "Bearer deterministic-e2e-key"
_EXPECTED_CONTROL_CREDENTIAL = "deterministic-e2e-control"
_PUBLISHER_BASE_URL = "http://publisher-agent:8004"
_MAX_PROXY_RESPONSE_BYTES = 512 * 1024
_PLAN_CONTENT = (
    '{"steps":['
    '{"kind":"prepare_data","title":"准备批准数据"},'
    '{"kind":"analyze_ndvi_change","title":"计算 NDVI 变化"},'
    '{"kind":"evaluate_quality","title":"核验成果质量"},'
    '{"kind":"publish_results","title":"发布地图与报告"}'
    "]}"
)


class _PublisherFailureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failures: int = Field(ge=0, le=1)


@dataclass(slots=True)
class _PublisherFailureController:
    failures_remaining: int = 0

    def consume(self) -> bool:
        if self.failures_remaining == 0:
            return False
        self.failures_remaining -= 1
        return True


def create_support_app(
    *,
    publisher_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    controller = _PublisherFailureController()

    @asynccontextmanager
    async def lifespan(support: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            base_url=_PUBLISHER_BASE_URL,
            timeout=httpx.Timeout(10),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            transport=publisher_transport,
        ) as publisher_client:
            support.state.publisher_client = publisher_client
            yield
            support.state.publisher_client = None

    support = FastAPI(
        title="Hennongxi E2E Support",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    support.state.publisher_failure_controller = controller

    @support.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @support.post("/v1/chat/completions")
    async def create_plan(
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        if authorization != _EXPECTED_AUTHORIZATION:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid E2E support credential",
            )
        return {
            "choices": [{"message": {"content": _PLAN_CONTENT}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 40},
        }

    @support.put(
        "/internal/e2e/v1/publisher-failure",
        status_code=status.HTTP_204_NO_CONTENT,
        include_in_schema=False,
    )
    async def configure_publisher_failure(
        payload: _PublisherFailureRequest,
        control_credential: Annotated[
            str | None,
            Header(alias="X-E2E-Control"),
        ] = None,
    ) -> Response:
        if control_credential != _EXPECTED_CONTROL_CREDENTIAL:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        controller.failures_remaining = payload.failures
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @support.get("/internal/v1/health", include_in_schema=False)
    async def proxy_publisher_health() -> Response:
        return await _proxy_request(support, "GET", "/internal/v1/health")

    @support.post("/internal/v1/publisher/publish", include_in_schema=False)
    async def proxy_publisher_publish(
        command: PublisherPublishCommand,
        correlation_id: Annotated[
            str | None,
            Header(alias="X-Correlation-ID"),
        ] = None,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> Response:
        if controller.consume():
            return _publisher_failure_response()
        headers = {
            key: value
            for key, value in {
                "X-Correlation-ID": correlation_id,
                "Idempotency-Key": idempotency_key,
                "Accept": "application/json",
            }.items()
            if value is not None
        }
        return await _proxy_request(
            support,
            "POST",
            "/internal/v1/publisher/publish",
            headers=headers,
            json=command.model_dump(mode="json"),
        )

    return support


async def _proxy_request(
    support: FastAPI,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    json: object | None = None,
) -> Response:
    client: httpx.AsyncClient = support.state.publisher_client
    try:
        response = await client.request(method, path, headers=headers, json=json)
    except httpx.HTTPError:
        return _publisher_failure_response()
    if len(response.content) > _MAX_PROXY_RESPONSE_BYTES:
        return _publisher_failure_response()
    content_type = response.headers.get("content-type", "application/json")
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers={"Content-Type": content_type},
    )


def _publisher_failure_response() -> JSONResponse:
    payload = ErrorResponse(
        error=StructuredError(
            code=ErrorCode.PUBLISHING_FAILED,
            message="Publisher E2E forced failure",
            retryable=True,
        )
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(mode="json"),
    )


app = create_support_app()
