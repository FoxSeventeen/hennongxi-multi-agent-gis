from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hennongxi_master.amap import (
    AMAP_ORIGIN,
    AmapConfig,
    AmapConfigurationError,
    AmapVerification,
    AmapVerificationCode,
)
from pydantic import ValidationError

FAKE_AMAP_KEY = "test-amap-key-private-value"
CHECKED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


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
