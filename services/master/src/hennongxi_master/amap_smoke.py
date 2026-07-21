"""Run one explicit real AMap verification smoke without exposing provider data."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal

import httpx
from hennongxi_contracts.common import UtcDateTime
from pydantic import BaseModel, ConfigDict, Field

from hennongxi_master.amap import (
    AMAP_ORIGIN,
    AmapConfig,
    AmapConfigurationError,
    AmapStudyAreaVerifier,
    AmapVerification,
    AmapVerificationCode,
)

_PROVIDER_SUCCESS_CODES = frozenset(
    {
        AmapVerificationCode.VERIFIED,
        AmapVerificationCode.NO_MATCH,
        AmapVerificationCode.AMBIGUOUS,
    }
)


class _SmokeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    provider_origin_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    code: AmapVerificationCode
    infocode: Literal["10000"] | None
    checked_at: UtcDateTime
    duration_ms: int = Field(ge=0)
    retryable: bool
    match_count: int = Field(ge=0, le=10)


@dataclass(frozen=True, slots=True)
class SmokeCommandResult:
    exit_code: int
    output: str


async def execute_smoke(
    *,
    environment: Mapping[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SmokeCommandResult:
    """Execute the fixed canonical lookup and return only JSON-safe evidence."""

    try:
        config = AmapConfig.from_environment(environment)
    except AmapConfigurationError as error:
        return _result(2, {"ok": False, "error_code": error.code})

    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(config.timeout_seconds),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": "application/json"},
        ) as client:
            verification = await AmapStudyAreaVerifier(config, client).verify()
    except Exception:
        return _result(3, {"ok": False, "error_code": "AMAP_SMOKE_INTERNAL_ERROR"})

    evidence = _evidence(verification)
    return SmokeCommandResult(
        exit_code=0 if verification.code is AmapVerificationCode.VERIFIED else 1,
        output=evidence.model_dump_json(),
    )


def _evidence(verification: AmapVerification) -> _SmokeEvidence:
    return _SmokeEvidence(
        ok=verification.code is AmapVerificationCode.VERIFIED,
        provider_origin_sha256=sha256(AMAP_ORIGIN.encode()).hexdigest(),
        code=verification.code,
        infocode="10000" if verification.code in _PROVIDER_SUCCESS_CODES else None,
        checked_at=verification.checked_at,
        duration_ms=verification.duration_ms,
        retryable=verification.retryable,
        match_count=verification.match_count,
    )


def _result(exit_code: int, payload: dict[str, object]) -> SmokeCommandResult:
    return SmokeCommandResult(
        exit_code=exit_code,
        output=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
    )


def main() -> int:
    result = asyncio.run(execute_smoke())
    stream = sys.stdout if result.exit_code == 0 else sys.stderr
    print(result.output, file=stream)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
