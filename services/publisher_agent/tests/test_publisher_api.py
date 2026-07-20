from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_contracts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    ErrorCode,
    ErrorResponse,
    TileArtifactType,
)
from hennongxi_observability import CORRELATION_ID_HEADER
from hennongxi_publisher_agent.catalog import (
    PublishedTileIntegrityError,
    PublishedTileNotFoundError,
    ResolvedTile,
)
from hennongxi_publisher_agent.main import app
from hennongxi_publisher_agent.tiles import (
    TileCoordinateError,
    TileOutsideSourceError,
    TileSourceError,
)
from structlog.testing import capture_logs

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\nverified"


class _StubCatalog:
    def __init__(self, outcome: ResolvedTile | Exception) -> None:
        self._outcome = outcome

    def resolve_tile(self, _task_id: UUID, _artifact_type: TileArtifactType) -> ResolvedTile:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _StubRenderer:
    def __init__(self, outcome: bytes | Exception) -> None:
        self._outcome = outcome

    def render(self, *_args: object, **_kwargs: object) -> bytes:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _resolved() -> ResolvedTile:
    return ResolvedTile(
        path=Path("/private/fixed/ndvi_before.tif"),
        artifact=ArtifactRef(
            artifact_id=uuid5(TASK_ID, "analysis:1:NDVI_BEFORE"),
            task_id=TASK_ID,
            attempt=1,
            artifact_type=ArtifactType.NDVI_BEFORE,
            status=ArtifactStatus.COMPLETE,
            media_type="image/tiff; application=geotiff",
            created_at=NOW,
            checksum_sha256="a" * 64,
            byte_size=100,
        ),
        attempt=1,
    )


@pytest.fixture
def configured_app() -> Iterator[FastAPI]:
    original_catalog = app.state.publisher_catalog
    original_renderer = app.state.tile_renderer
    app.state.publisher_catalog = _StubCatalog(_resolved())
    app.state.tile_renderer = _StubRenderer(PNG)
    try:
        yield app
    finally:
        app.state.publisher_catalog = original_catalog
        app.state.tile_renderer = original_renderer


def _tile_path(artifact_type: str = "NDVI_BEFORE") -> str:
    return f"/api/v1/tiles/{TASK_ID}/{artifact_type}/8/206/104.png"


def test_tile_route_returns_png_cache_security_cors_and_correlated_logs(
    configured_app: FastAPI,
) -> None:
    with capture_logs() as logs, TestClient(configured_app) as client:
        response = client.get(
            _tile_path(),
            headers={
                CORRELATION_ID_HEADER: str(CORRELATION_ID),
                "Origin": "http://127.0.0.1:3000",
            },
        )

    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=60, must-revalidate"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    served = next(log for log in logs if log["event"] == "publisher_tile_served")
    assert served["task_id"] == str(TASK_ID)
    assert served["artifact_type"] == "NDVI_BEFORE"
    assert "/private/" not in str(logs)


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (PublishedTileNotFoundError("private"), 404, ErrorCode.TASK_NOT_FOUND),
        (TileOutsideSourceError("private"), 404, ErrorCode.TASK_NOT_FOUND),
        (PublishedTileIntegrityError("private"), 409, ErrorCode.PUBLISHING_FAILED),
        (TileSourceError("private"), 409, ErrorCode.PUBLISHING_FAILED),
        (TileCoordinateError("private"), 422, ErrorCode.VALIDATION_ERROR),
    ],
)
def test_tile_route_maps_expected_failures_without_private_details(
    configured_app: FastAPI,
    failure: Exception,
    status_code: int,
    code: ErrorCode,
) -> None:
    if isinstance(failure, PublishedTileNotFoundError | PublishedTileIntegrityError):
        configured_app.state.publisher_catalog = _StubCatalog(failure)
    else:
        configured_app.state.tile_renderer = _StubRenderer(failure)

    with TestClient(configured_app) as client:
        response = client.get(_tile_path())

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/json")
    assert error.error.code is code
    assert "private" not in response.text


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/tiles/{TASK_ID}/NOT_ALLOWED/8/206/104.png",
        "/api/v1/tiles/not-a-uuid/NDVI_BEFORE/8/206/104.png",
        "/api/v1/tiles/unknown",
    ],
)
def test_tile_route_returns_structured_errors_for_invalid_or_unknown_paths(
    configured_app: FastAPI,
    path: str,
) -> None:
    with TestClient(configured_app) as client:
        response = client.get(path)

    assert response.status_code in {404, 422}
    ErrorResponse.model_validate(response.json())


def test_tile_route_sanitizes_unexpected_failures(configured_app: FastAPI) -> None:
    configured_app.state.tile_renderer = _StubRenderer(RuntimeError("secret /tmp/private"))
    with TestClient(configured_app, raise_server_exceptions=False) as client:
        response = client.get(_tile_path())

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 500
    assert error.error.code is ErrorCode.INTERNAL_ERROR
    assert "secret" not in response.text.lower()
    assert "/tmp/private" not in response.text
