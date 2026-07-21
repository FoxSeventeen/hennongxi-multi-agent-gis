from __future__ import annotations

import json

import httpx
import pytest
from hennongxi_master.amap import (
    CANONICAL_STUDY_AREA_ADCODE,
    CANONICAL_STUDY_AREA_NAME,
    AmapVerificationCode,
)
from hennongxi_master.amap_smoke import execute_smoke

FAKE_AMAP_KEY = "test-smoke-amap-key-private-value"
PRIVATE_POI_ID = "private-smoke-amap-poi-id"
PRIVATE_LOCATION = "110.123456,31.123456"


def smoke_environment() -> dict[str, str]:
    return {
        "AMAP_WEB_SERVICE_KEY": FAKE_AMAP_KEY,
        "AMAP_TIMEOUT_SECONDS": "4",
    }


def provider_response() -> bytes:
    return json.dumps(
        {
            "status": "1",
            "info": "OK",
            "infocode": "10000",
            "count": "1",
            "pois": [
                {
                    "id": PRIVATE_POI_ID,
                    "name": CANONICAL_STUDY_AREA_NAME,
                    "adcode": CANONICAL_STUDY_AREA_ADCODE,
                    "typecode": "110202",
                    "location": PRIVATE_LOCATION,
                }
            ],
        },
        ensure_ascii=False,
    ).encode()


@pytest.mark.asyncio
async def test_amap_smoke_returns_only_sanitized_success_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=provider_response())

    result = await execute_smoke(
        environment=smoke_environment(),
        transport=httpx.MockTransport(handler),
    )

    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["ok"] is True
    assert output["code"] == AmapVerificationCode.VERIFIED
    assert output["infocode"] == "10000"
    assert output["retryable"] is False
    assert output["match_count"] == 1
    assert output["duration_ms"] >= 0
    assert len(output["provider_origin_sha256"]) == 64
    assert set(output) == {
        "ok",
        "provider_origin_sha256",
        "code",
        "infocode",
        "checked_at",
        "duration_ms",
        "retryable",
        "match_count",
    }
    for private_value in (
        FAKE_AMAP_KEY,
        PRIVATE_POI_ID,
        PRIVATE_LOCATION,
        CANONICAL_STUDY_AREA_NAME,
        CANONICAL_STUDY_AREA_ADCODE,
        "restapi.amap.com",
    ):
        assert private_value not in result.output


@pytest.mark.asyncio
async def test_amap_smoke_returns_sanitized_provider_failure() -> None:
    private_error = "private-provider-error-detail"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "0", "info": private_error, "infocode": "10001"},
        )

    result = await execute_smoke(
        environment=smoke_environment(),
        transport=httpx.MockTransport(handler),
    )

    assert result.exit_code == 1
    output = json.loads(result.output)
    assert output["ok"] is False
    assert output["code"] == AmapVerificationCode.AUTHENTICATION_FAILED
    assert output["infocode"] is None
    assert output["retryable"] is False
    assert output["match_count"] == 0
    assert private_error not in result.output
    assert FAKE_AMAP_KEY not in result.output


@pytest.mark.asyncio
async def test_amap_smoke_reports_missing_configuration_without_echoing_environment() -> None:
    result = await execute_smoke(
        environment={
            "AMAP_WEB_SERVICE_KEY": "   ",
            "UNSUPPORTED_PRIVATE_VALUE": "private-unsupported-value",
        }
    )

    assert result.exit_code == 2
    assert json.loads(result.output) == {
        "ok": False,
        "error_code": "AMAP_NOT_CONFIGURED",
    }
    assert "private-unsupported-value" not in result.output


@pytest.mark.asyncio
async def test_amap_smoke_sanitizes_unexpected_internal_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("private-unexpected-internal-detail")

    result = await execute_smoke(
        environment=smoke_environment(),
        transport=httpx.MockTransport(handler),
    )

    assert result.exit_code == 3
    assert json.loads(result.output) == {
        "ok": False,
        "error_code": "AMAP_SMOKE_INTERNAL_ERROR",
    }
    assert FAKE_AMAP_KEY not in result.output
    assert "private-unexpected-internal-detail" not in result.output
