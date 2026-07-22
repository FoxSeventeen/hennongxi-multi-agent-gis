from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from hennongxi_master.amap import (
    AMAP_ORIGIN,
    AMAP_PLACE_PATH,
    CANONICAL_STUDY_AREA_ADCODE,
    CANONICAL_STUDY_AREA_NAME,
    MAX_AMAP_RESPONSE_BYTES,
    AmapConfig,
    AmapConfigurationError,
    AmapStudyAreaVerifier,
    AmapVerification,
    AmapVerificationCode,
)
from pydantic import ValidationError

FAKE_AMAP_KEY = "test-amap-key-private-value"
CHECKED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def amap_config() -> AmapConfig:
    return AmapConfig.from_environment(
        {
            "AMAP_WEB_SERVICE_KEY": FAKE_AMAP_KEY,
            "AMAP_TIMEOUT_SECONDS": "4.5",
        }
    )


def amap_response(pois: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "status": "1",
            "info": "OK",
            "infocode": "10000",
            "count": str(len(pois)),
            "pois": pois,
            "private_provider_extension": "must-not-be-retained",
        },
        ensure_ascii=False,
    ).encode()


def amap_error_response(infocode: str) -> bytes:
    return json.dumps(
        {
            "status": "0",
            "info": "private-amap-provider-error-detail",
            "infocode": infocode,
        }
    ).encode()


def canonical_poi(**overrides: object) -> dict[str, object]:
    poi: dict[str, object] = {
        "id": "private-amap-poi-id",
        "name": CANONICAL_STUDY_AREA_NAME,
        "adcode": CANONICAL_STUDY_AREA_ADCODE,
        "typecode": "110202",
        "location": "110.123456,31.123456",
        "address": "private-amap-address",
    }
    poi.update(overrides)
    return poi


class FailIfReadStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise AssertionError("non-200 provider body must not be read")
        yield b"unreachable-private-provider-body"


def test_amap_config_loads_only_key_and_bounded_timeout_without_exposing_secret() -> None:
    config = AmapConfig.from_environment(
        {
            "AMAP_WEB_SERVICE_KEY": FAKE_AMAP_KEY,
            "AMAP_TIMEOUT_SECONDS": "4.5",
            "AMAP_BASE_URL": "http://127.0.0.1:5432/private",
        }
    )

    assert config.api_key.get_secret_value() == FAKE_AMAP_KEY
    assert config.timeout_seconds == 4.5
    assert AMAP_ORIGIN == "https://restapi.amap.com"
    assert FAKE_AMAP_KEY not in repr(config)
    assert "127.0.0.1" not in repr(config)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"AMAP_WEB_SERVICE_KEY": "   "},
        {"AMAP_WEB_SERVICE_KEY": FAKE_AMAP_KEY, "AMAP_TIMEOUT_SECONDS": "0"},
        {"AMAP_WEB_SERVICE_KEY": FAKE_AMAP_KEY, "AMAP_TIMEOUT_SECONDS": "11"},
    ],
)
def test_amap_config_rejects_missing_or_unsafe_values_without_echoing_them(
    environment: dict[str, str],
) -> None:
    with pytest.raises(AmapConfigurationError) as raised:
        AmapConfig.from_environment(environment)

    assert raised.value.code == "AMAP_NOT_CONFIGURED"
    assert str(raised.value) == "AMap Web Service configuration is invalid or incomplete"
    assert FAKE_AMAP_KEY not in str(raised.value)
    assert FAKE_AMAP_KEY not in repr(raised.value)


@pytest.mark.parametrize(
    ("code", "match_count", "retryable"),
    [
        (AmapVerificationCode.VERIFIED, 1, False),
        (AmapVerificationCode.NO_MATCH, 0, False),
        (AmapVerificationCode.AMBIGUOUS, 2, False),
        (AmapVerificationCode.AUTHENTICATION_FAILED, 0, False),
        (AmapVerificationCode.QUOTA_EXCEEDED, 0, True),
        (AmapVerificationCode.RATE_LIMITED, 0, True),
        (AmapVerificationCode.PROVIDER_UNAVAILABLE, 0, True),
        (AmapVerificationCode.REQUEST_REJECTED, 0, False),
        (AmapVerificationCode.RESPONSE_INVALID, 0, True),
    ],
)
def test_amap_verification_accepts_only_sanitized_consistent_evidence(
    code: AmapVerificationCode,
    match_count: int,
    retryable: bool,
) -> None:
    result = AmapVerification(
        code=code,
        checked_at=CHECKED_AT,
        duration_ms=12,
        retryable=retryable,
        match_count=match_count,
    )

    serialized = result.model_dump(mode="json")
    assert serialized == {
        "code": code.value,
        "checked_at": "2026-07-21T12:00:00Z",
        "duration_ms": 12,
        "retryable": retryable,
        "match_count": match_count,
    }
    assert set(serialized) == {
        "code",
        "checked_at",
        "duration_ms",
        "retryable",
        "match_count",
    }


@pytest.mark.parametrize(
    ("code", "match_count", "retryable"),
    [
        (AmapVerificationCode.VERIFIED, 0, False),
        (AmapVerificationCode.VERIFIED, 1, True),
        (AmapVerificationCode.NO_MATCH, 1, False),
        (AmapVerificationCode.AMBIGUOUS, 1, False),
        (AmapVerificationCode.AUTHENTICATION_FAILED, 0, True),
        (AmapVerificationCode.PROVIDER_UNAVAILABLE, 0, False),
    ],
)
def test_amap_verification_rejects_inconsistent_evidence(
    code: AmapVerificationCode,
    match_count: int,
    retryable: bool,
) -> None:
    with pytest.raises(ValidationError):
        AmapVerification(
            code=code,
            checked_at=CHECKED_AT,
            duration_ms=0,
            retryable=retryable,
            match_count=match_count,
        )


@pytest.mark.asyncio
async def test_amap_verifier_uses_only_fixed_canonical_search_and_returns_minimal_evidence() -> (
    None
):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, content=amap_response([canonical_poi()]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "GET"
    assert request.headers["accept"] == "application/json"
    assert request.headers["accept-encoding"] == "identity"
    assert request.url.scheme == "https"
    assert request.url.host == "restapi.amap.com"
    assert request.url.path == AMAP_PLACE_PATH
    assert dict(request.url.params) == {
        "key": FAKE_AMAP_KEY,
        "keywords": CANONICAL_STUDY_AREA_NAME,
        "types": "110000",
        "city": CANONICAL_STUDY_AREA_ADCODE,
        "citylimit": "true",
        "offset": "10",
        "page": "1",
        "extensions": "all",
        "output": "JSON",
    }
    assert result.code is AmapVerificationCode.VERIFIED
    assert result.match_count == 1
    assert result.retryable is False
    assert result.checked_at.tzinfo is UTC
    assert result.duration_ms >= 0

    serialized = result.model_dump_json()
    assert FAKE_AMAP_KEY not in serialized
    assert "private-amap-poi-id" not in serialized
    assert "private-amap-address" not in serialized
    assert "110.123456" not in serialized
    assert "must-not-be-retained" not in serialized


@pytest.mark.parametrize(
    ("pois", "expected_code", "expected_match_count"),
    [
        ([], AmapVerificationCode.NO_MATCH, 0),
        (
            [canonical_poi(name="神农溪风景区")],
            AmapVerificationCode.NO_MATCH,
            0,
        ),
        (
            [canonical_poi(adcode="422822")],
            AmapVerificationCode.NO_MATCH,
            0,
        ),
        (
            [canonical_poi(typecode="060101")],
            AmapVerificationCode.NO_MATCH,
            0,
        ),
        (
            [canonical_poi(), canonical_poi(id="second-private-id")],
            AmapVerificationCode.AMBIGUOUS,
            2,
        ),
    ],
)
@pytest.mark.asyncio
async def test_amap_verifier_counts_only_exact_canonical_scenic_matches(
    pois: list[dict[str, object]],
    expected_code: AmapVerificationCode,
    expected_match_count: int,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=amap_response(pois))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    assert result.code is expected_code
    assert result.match_count == expected_match_count
    assert result.retryable is False


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (400, AmapVerificationCode.REQUEST_REJECTED, False),
        (401, AmapVerificationCode.AUTHENTICATION_FAILED, False),
        (403, AmapVerificationCode.AUTHENTICATION_FAILED, False),
        (429, AmapVerificationCode.RATE_LIMITED, True),
        (500, AmapVerificationCode.PROVIDER_UNAVAILABLE, True),
        (503, AmapVerificationCode.PROVIDER_UNAVAILABLE, True),
    ],
)
@pytest.mark.asyncio
async def test_amap_verifier_maps_http_status_without_retaining_provider_body(
    status_code: int,
    expected_code: AmapVerificationCode,
    retryable: bool,
) -> None:
    private_detail = "private-http-response-body"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=private_detail)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    assert result.code is expected_code
    assert result.retryable is retryable
    assert result.match_count == 0
    assert private_detail not in result.model_dump_json()


@pytest.mark.parametrize(
    ("infocode", "expected_code", "retryable"),
    [
        ("10001", AmapVerificationCode.AUTHENTICATION_FAILED, False),
        ("10002", AmapVerificationCode.AUTHENTICATION_FAILED, False),
        ("10009", AmapVerificationCode.AUTHENTICATION_FAILED, False),
        ("10013", AmapVerificationCode.AUTHENTICATION_FAILED, False),
        ("10026", AmapVerificationCode.AUTHENTICATION_FAILED, False),
        ("10041", AmapVerificationCode.AUTHENTICATION_FAILED, False),
        ("10003", AmapVerificationCode.QUOTA_EXCEEDED, True),
        ("10044", AmapVerificationCode.QUOTA_EXCEEDED, True),
        ("10045", AmapVerificationCode.QUOTA_EXCEEDED, True),
        ("40000", AmapVerificationCode.QUOTA_EXCEEDED, True),
        ("40002", AmapVerificationCode.QUOTA_EXCEEDED, True),
        ("10004", AmapVerificationCode.RATE_LIMITED, True),
        ("10010", AmapVerificationCode.RATE_LIMITED, True),
        ("10014", AmapVerificationCode.RATE_LIMITED, True),
        ("10021", AmapVerificationCode.RATE_LIMITED, True),
        ("10029", AmapVerificationCode.RATE_LIMITED, True),
        ("10016", AmapVerificationCode.PROVIDER_UNAVAILABLE, True),
        ("10017", AmapVerificationCode.PROVIDER_UNAVAILABLE, True),
        ("20003", AmapVerificationCode.PROVIDER_UNAVAILABLE, True),
        ("30001", AmapVerificationCode.PROVIDER_UNAVAILABLE, True),
        ("20000", AmapVerificationCode.REQUEST_REJECTED, False),
        ("20001", AmapVerificationCode.REQUEST_REJECTED, False),
        ("20002", AmapVerificationCode.REQUEST_REJECTED, False),
        ("20012", AmapVerificationCode.REQUEST_REJECTED, False),
        ("99999", AmapVerificationCode.RESPONSE_INVALID, True),
    ],
)
@pytest.mark.asyncio
async def test_amap_verifier_maps_provider_infocode_to_sanitized_outcome(
    infocode: str,
    expected_code: AmapVerificationCode,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=amap_error_response(infocode))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    assert result.code is expected_code
    assert result.retryable is retryable
    assert result.match_count == 0
    assert "private-amap-provider-error-detail" not in result.model_dump_json()
    assert infocode not in result.model_dump_json()


@pytest.mark.parametrize("exception_type", [httpx.ReadTimeout, httpx.ConnectError])
@pytest.mark.asyncio
async def test_amap_verifier_sanitizes_transport_failures(
    exception_type: type[httpx.RequestError],
) -> None:
    private_detail = "private-transport-error-detail"

    def handler(request: httpx.Request) -> httpx.Response:
        raise exception_type(private_detail, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    assert result.code is AmapVerificationCode.PROVIDER_UNAVAILABLE
    assert result.retryable is True
    assert private_detail not in str(result)
    assert private_detail not in result.model_dump_json()


@pytest.mark.parametrize(
    "content",
    [
        b"not-json-private-response",
        json.dumps({"status": "1", "info": "OK", "infocode": "10000"}).encode(),
        b"x" * (MAX_AMAP_RESPONSE_BYTES + 1),
    ],
)
@pytest.mark.asyncio
async def test_amap_verifier_rejects_malformed_or_oversized_response_without_retaining_it(
    content: bytes,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    assert result.code is AmapVerificationCode.RESPONSE_INVALID
    assert result.retryable is True
    assert result.match_count == 0
    assert "not-json-private-response" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_amap_verifier_does_not_follow_redirects() -> None:
    requested_hosts: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    assert requested_hosts == ["restapi.amap.com"]
    assert result.code is AmapVerificationCode.REQUEST_REJECTED
    assert result.retryable is False


@pytest.mark.asyncio
async def test_amap_verifier_never_reads_non_200_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, stream=FailIfReadStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    assert result.code is AmapVerificationCode.REQUEST_REJECTED


@pytest.mark.parametrize(
    "headers",
    [
        {"content-encoding": "gzip"},
        {"content-encoding": ""},
        {"content-encoding": "\x0bidentity"},
        {"content-length": str(MAX_AMAP_RESPONSE_BYTES + 1)},
        {"content-length": "1_0"},
        {"content-length": "9" * 5_000},
    ],
)
@pytest.mark.asyncio
async def test_amap_verifier_rejects_encoded_or_declared_oversized_body_without_reading(
    headers: dict[str, str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=FailIfReadStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await AmapStudyAreaVerifier(amap_config(), client).verify()

    assert result.code is AmapVerificationCode.RESPONSE_INVALID
    assert result.retryable is True
