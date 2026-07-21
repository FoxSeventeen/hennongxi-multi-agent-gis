"""Safe primitives for optional AMap Web Service study-area verification."""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from typing import Final, Self

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
