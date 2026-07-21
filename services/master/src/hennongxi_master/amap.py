"""Safe primitives for optional AMap Web Service study-area verification."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Self

import httpx
from hennongxi_contracts.common import UtcDateTime
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

# AMap Web Service POI search endpoint. The origin is intentionally not configurable.
# Source: https://lbs.amap.com/api/webservice/guide/api/search/
AMAP_ORIGIN: Final = "https://restapi.amap.com"
AMAP_PLACE_PATH: Final = "/v3/place/text"
CANONICAL_STUDY_AREA_NAME: Final = "神农溪景区"
CANONICAL_STUDY_AREA_ADCODE: Final = "422823"
AMAP_SCENIC_TYPE_PREFIX: Final = "11"
MAX_AMAP_RESPONSE_BYTES: Final = 64 * 1024
DEFAULT_AMAP_TIMEOUT_SECONDS: Final = 3.0
MAX_AMAP_TIMEOUT_SECONDS: Final = 10.0


class AmapConfigurationError(ValueError):
    """A configuration failure that never echoes the configured credential."""

    code = "AMAP_NOT_CONFIGURED"

    def __init__(self) -> None:
        super().__init__("AMap Web Service configuration is invalid or incomplete")


class AmapConfig(BaseModel):
    """Validated backend-only AMap configuration with a redacted representation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    api_key: SecretStr = Field(min_length=1, repr=False)
    timeout_seconds: float = Field(
        default=DEFAULT_AMAP_TIMEOUT_SECONDS,
        gt=0,
        le=MAX_AMAP_TIMEOUT_SECONDS,
    )

    @field_validator("api_key")
    @classmethod
    def reject_blank_key(cls, value: SecretStr) -> SecretStr:
        normalized = value.get_secret_value().strip()
        if not normalized:
            raise ValueError("AMAP_WEB_SERVICE_KEY cannot be blank")
        return SecretStr(normalized)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Self:
        values = os.environ if environment is None else environment
        try:
            return cls.model_validate(
                {
                    "api_key": values.get("AMAP_WEB_SERVICE_KEY"),
                    "timeout_seconds": values.get(
                        "AMAP_TIMEOUT_SECONDS",
                        str(DEFAULT_AMAP_TIMEOUT_SECONDS),
                    ),
                }
            )
        except ValidationError:
            raise AmapConfigurationError() from None


class AmapVerificationCode(StrEnum):
    """Provider-neutral outcomes safe for task evidence and logs."""

    VERIFIED = "VERIFIED"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    RESPONSE_INVALID = "RESPONSE_INVALID"


_RETRYABLE_CODES: Final = frozenset(
    {
        AmapVerificationCode.QUOTA_EXCEEDED,
        AmapVerificationCode.RATE_LIMITED,
        AmapVerificationCode.PROVIDER_UNAVAILABLE,
        AmapVerificationCode.RESPONSE_INVALID,
    }
)


class AmapVerification(BaseModel):
    """Minimal evidence that cannot retain AMap POI, coordinates, or response bodies."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    code: AmapVerificationCode
    checked_at: UtcDateTime
    duration_ms: int = Field(ge=0)
    retryable: bool
    match_count: int = Field(ge=0, le=10)

    @model_validator(mode="after")
    def require_consistent_outcome(self) -> Self:
        if self.code is AmapVerificationCode.VERIFIED and self.match_count != 1:
            raise ValueError("verified result requires exactly one canonical match")
        if self.code is AmapVerificationCode.AMBIGUOUS and self.match_count < 2:
            raise ValueError("ambiguous result requires at least two canonical matches")
        if (
            self.code
            not in {
                AmapVerificationCode.VERIFIED,
                AmapVerificationCode.AMBIGUOUS,
            }
            and self.match_count != 0
        ):
            raise ValueError("non-matching result cannot retain a match count")
        if self.retryable != (self.code in _RETRYABLE_CODES):
            raise ValueError("retryability must match the sanitized result code")
        return self


class _AmapPoi(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    adcode: str = Field(min_length=1, max_length=20)
    typecode: str = Field(min_length=1, max_length=200)


class _AmapResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    status: str
    info: str
    infocode: str
    pois: tuple[_AmapPoi, ...] = Field(max_length=10)


class _AmapResponseTooLarge(ValueError):
    pass


@dataclass(slots=True, repr=False)
class AmapStudyAreaVerifier:
    """Verify the one approved study area without accepting request parameters."""

    config: AmapConfig
    client: httpx.AsyncClient

    async def verify(self) -> AmapVerification:
        checked_at = datetime.now(UTC)
        timer_started = time.monotonic()

        try:
            async with self.client.stream(
                "GET",
                f"{AMAP_ORIGIN}{AMAP_PLACE_PATH}",
                params={
                    "key": self.config.api_key.get_secret_value(),
                    "keywords": CANONICAL_STUDY_AREA_NAME,
                    "types": "110000",
                    "city": CANONICAL_STUDY_AREA_ADCODE,
                    "citylimit": "true",
                    "offset": "10",
                    "page": "1",
                    "extensions": "all",
                    "output": "JSON",
                },
                timeout=httpx.Timeout(self.config.timeout_seconds),
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    return self._result(
                        code=AmapVerificationCode.RESPONSE_INVALID,
                        checked_at=checked_at,
                        timer_started=timer_started,
                    )
                response_body = await _read_bounded_response(response)
        except (httpx.HTTPError, _AmapResponseTooLarge):
            return self._result(
                code=AmapVerificationCode.RESPONSE_INVALID,
                checked_at=checked_at,
                timer_started=timer_started,
            )

        try:
            provider_response = _AmapResponse.model_validate_json(response_body)
        except ValidationError:
            return self._result(
                code=AmapVerificationCode.RESPONSE_INVALID,
                checked_at=checked_at,
                timer_started=timer_started,
            )

        if (
            provider_response.status != "1"
            or provider_response.info != "OK"
            or provider_response.infocode != "10000"
        ):
            return self._result(
                code=AmapVerificationCode.RESPONSE_INVALID,
                checked_at=checked_at,
                timer_started=timer_started,
            )

        match_count = sum(_is_canonical_match(poi) for poi in provider_response.pois)
        if match_count == 1:
            code = AmapVerificationCode.VERIFIED
        elif match_count > 1:
            code = AmapVerificationCode.AMBIGUOUS
        else:
            code = AmapVerificationCode.NO_MATCH
        return self._result(
            code=code,
            checked_at=checked_at,
            timer_started=timer_started,
            match_count=match_count,
        )

    @staticmethod
    def _result(
        *,
        code: AmapVerificationCode,
        checked_at: datetime,
        timer_started: float,
        match_count: int = 0,
    ) -> AmapVerification:
        return AmapVerification(
            code=code,
            checked_at=checked_at,
            duration_ms=max(0, round((time.monotonic() - timer_started) * 1000)),
            retryable=code in _RETRYABLE_CODES,
            match_count=match_count,
        )


def _is_canonical_match(poi: _AmapPoi) -> bool:
    return (
        poi.name == CANONICAL_STUDY_AREA_NAME
        and poi.adcode == CANONICAL_STUDY_AREA_ADCODE
        and any(
            type_code.startswith(AMAP_SCENIC_TYPE_PREFIX) for type_code in poi.typecode.split(";")
        )
    )


async def _read_bounded_response(response: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > MAX_AMAP_RESPONSE_BYTES:
            raise _AmapResponseTooLarge
        body.extend(chunk)
    return bytes(body)
