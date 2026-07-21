"""Provider-neutral study-area intent and evidence primitives."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Final, Protocol, Self

from hennongxi_contracts.common import UtcDateTime
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hennongxi_master.amap import AmapVerification, AmapVerificationCode


class StudyAreaIntent(StrEnum):
    """Local classification performed without sending user text to a provider."""

    APPROVED = "APPROVED"
    AMBIGUOUS = "AMBIGUOUS"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class StudyAreaConclusion(StrEnum):
    """Provider-neutral conclusions safe to retain as task evidence."""

    VERIFIED = "VERIFIED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


class StudyAreaReasonCode(StrEnum):
    """Sanitized reasons that never contain provider payloads or user input."""

    ONLINE_MATCH_CONFIRMED = "ONLINE_MATCH_CONFIRMED"
    LOCAL_STUDY_AREA_AMBIGUOUS = "LOCAL_STUDY_AREA_AMBIGUOUS"
    OUT_OF_SCOPE_STUDY_AREA = "OUT_OF_SCOPE_STUDY_AREA"
    ONLINE_CHECK_NOT_CONFIGURED = "ONLINE_CHECK_NOT_CONFIGURED"
    ONLINE_NO_MATCH = "ONLINE_NO_MATCH"
    ONLINE_MATCH_AMBIGUOUS = "ONLINE_MATCH_AMBIGUOUS"
    ONLINE_AUTHENTICATION_FAILED = "ONLINE_AUTHENTICATION_FAILED"
    ONLINE_QUOTA_EXCEEDED = "ONLINE_QUOTA_EXCEEDED"
    ONLINE_RATE_LIMITED = "ONLINE_RATE_LIMITED"
    ONLINE_CHECK_UNAVAILABLE = "ONLINE_CHECK_UNAVAILABLE"
    ONLINE_REQUEST_REJECTED = "ONLINE_REQUEST_REJECTED"
    ONLINE_RESPONSE_INVALID = "ONLINE_RESPONSE_INVALID"


_VERIFIED_REASONS: Final = frozenset({StudyAreaReasonCode.ONLINE_MATCH_CONFIRMED})
_REJECTED_REASONS: Final = frozenset({StudyAreaReasonCode.OUT_OF_SCOPE_STUDY_AREA})
_RETRYABLE_REASONS: Final = frozenset(
    {
        StudyAreaReasonCode.ONLINE_QUOTA_EXCEEDED,
        StudyAreaReasonCode.ONLINE_RATE_LIMITED,
        StudyAreaReasonCode.ONLINE_CHECK_UNAVAILABLE,
        StudyAreaReasonCode.ONLINE_RESPONSE_INVALID,
    }
)
_ONLINE_REASON_BY_CODE: Final = {
    AmapVerificationCode.VERIFIED: StudyAreaReasonCode.ONLINE_MATCH_CONFIRMED,
    AmapVerificationCode.NO_MATCH: StudyAreaReasonCode.ONLINE_NO_MATCH,
    AmapVerificationCode.AMBIGUOUS: StudyAreaReasonCode.ONLINE_MATCH_AMBIGUOUS,
    AmapVerificationCode.AUTHENTICATION_FAILED: (StudyAreaReasonCode.ONLINE_AUTHENTICATION_FAILED),
    AmapVerificationCode.QUOTA_EXCEEDED: StudyAreaReasonCode.ONLINE_QUOTA_EXCEEDED,
    AmapVerificationCode.RATE_LIMITED: StudyAreaReasonCode.ONLINE_RATE_LIMITED,
    AmapVerificationCode.PROVIDER_UNAVAILABLE: StudyAreaReasonCode.ONLINE_CHECK_UNAVAILABLE,
    AmapVerificationCode.REQUEST_REJECTED: StudyAreaReasonCode.ONLINE_REQUEST_REJECTED,
    AmapVerificationCode.RESPONSE_INVALID: StudyAreaReasonCode.ONLINE_RESPONSE_INVALID,
}


class StudyAreaEvidence(BaseModel):
    """Minimal durable evidence with no query, credential, POI, or response data."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    conclusion: StudyAreaConclusion
    checked_at: UtcDateTime
    duration_ms: int = Field(ge=0)
    reason_code: StudyAreaReasonCode
    retryable: bool

    @model_validator(mode="after")
    def require_consistent_conclusion(self) -> Self:
        if self.conclusion is StudyAreaConclusion.VERIFIED:
            allowed_reasons = _VERIFIED_REASONS
        elif self.conclusion is StudyAreaConclusion.REJECTED:
            allowed_reasons = _REJECTED_REASONS
        else:
            allowed_reasons = frozenset(StudyAreaReasonCode) - _VERIFIED_REASONS - _REJECTED_REASONS

        if self.reason_code not in allowed_reasons:
            raise ValueError("study-area conclusion does not match its sanitized reason")
        if self.retryable != (self.reason_code in _RETRYABLE_REASONS):
            raise ValueError("retryability must match the sanitized study-area reason")
        return self


class SafeOnlineStudyAreaVerifier(Protocol):
    async def verify(self) -> AmapVerification: ...


@dataclass(frozen=True, slots=True, repr=False)
class StudyAreaGrounder:
    """Combine local intent with an optional fixed-input online cross-check."""

    online_verifier: SafeOnlineStudyAreaVerifier | None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    timer: Callable[[], float] = monotonic

    async def verify_query(self, query: str) -> StudyAreaEvidence:
        intent = resolve_study_area(query)
        checked_at = self.now()
        if intent is StudyAreaIntent.AMBIGUOUS:
            return _local_evidence(
                StudyAreaConclusion.DEGRADED,
                StudyAreaReasonCode.LOCAL_STUDY_AREA_AMBIGUOUS,
                checked_at,
            )
        if intent is StudyAreaIntent.OUT_OF_SCOPE:
            return _local_evidence(
                StudyAreaConclusion.REJECTED,
                StudyAreaReasonCode.OUT_OF_SCOPE_STUDY_AREA,
                checked_at,
            )
        if self.online_verifier is None:
            return _local_evidence(
                StudyAreaConclusion.DEGRADED,
                StudyAreaReasonCode.ONLINE_CHECK_NOT_CONFIGURED,
                checked_at,
            )

        started = self.timer()
        try:
            online_result = await self.online_verifier.verify()
        except Exception:
            return StudyAreaEvidence(
                conclusion=StudyAreaConclusion.DEGRADED,
                checked_at=checked_at,
                duration_ms=max(0, round((self.timer() - started) * 1000)),
                reason_code=StudyAreaReasonCode.ONLINE_CHECK_UNAVAILABLE,
                retryable=True,
            )

        reason_code = _ONLINE_REASON_BY_CODE[online_result.code]
        return StudyAreaEvidence(
            conclusion=(
                StudyAreaConclusion.VERIFIED
                if online_result.code is AmapVerificationCode.VERIFIED
                else StudyAreaConclusion.DEGRADED
            ),
            checked_at=online_result.checked_at,
            duration_ms=online_result.duration_ms,
            reason_code=reason_code,
            retryable=reason_code in _RETRYABLE_REASONS,
        )


def _local_evidence(
    conclusion: StudyAreaConclusion,
    reason_code: StudyAreaReasonCode,
    checked_at: datetime,
) -> StudyAreaEvidence:
    return StudyAreaEvidence(
        conclusion=conclusion,
        checked_at=checked_at,
        duration_ms=0,
        reason_code=reason_code,
        retryable=False,
    )


_APPROVED_ALIASES: Final = (
    "神农溪流域",
    "神农溪景区",
    "巴东县",
    "神农溪",
    "巴东",
)
_LOCATION_PATTERN: Final = re.compile(
    r"(?P<place>[\u4e00-\u9fff]{2,12}?(?:自治州|流域|景区|省|市|县))"
)
_LEADING_WORDS: Final = (
    "请生成",
    "请分析",
    "请监测",
    "请评估",
    "请研究",
    "请查看",
    "请对比",
    "生成",
    "分析",
    "监测",
    "评估",
    "研究",
    "查看",
    "对比",
    "针对",
    "关于",
    "位于",
    "以及",
    "并与",
    "和",
    "与",
    "及",
    "请",
)
_GENERIC_LOCATION_NAMES: Final = frozenset(
    {
        "生态",
        "监测",
        "研究",
        "目标",
        "项目",
        "数据",
        "变化",
        "分析",
    }
)


def resolve_study_area(query: str) -> StudyAreaIntent:
    """Classify only high-confidence locations; uncertain text stays ambiguous."""

    approved_alias_found = any(alias in query for alias in _APPROVED_ALIASES)
    explicit_locations = _extract_explicit_locations(query)
    conflicting_location_found = any(
        not any(alias in location for alias in _APPROVED_ALIASES) for location in explicit_locations
    )

    if conflicting_location_found or (explicit_locations and not approved_alias_found):
        return StudyAreaIntent.OUT_OF_SCOPE
    if approved_alias_found:
        return StudyAreaIntent.APPROVED
    return StudyAreaIntent.AMBIGUOUS


def _extract_explicit_locations(query: str) -> tuple[str, ...]:
    locations: list[str] = []
    for match in _LOCATION_PATTERN.finditer(query):
        location = _strip_leading_words(match.group("place"))
        suffix_length = (
            3 if location.endswith("自治州") else 2 if location.endswith(("流域", "景区")) else 1
        )
        name = location[:-suffix_length]
        if len(name) < 2 or name in _GENERIC_LOCATION_NAMES:
            continue
        locations.append(location)
    return tuple(locations)


def _strip_leading_words(value: str) -> str:
    normalized = value
    stripped = True
    while stripped:
        stripped = False
        for word in _LEADING_WORDS:
            if normalized.startswith(word):
                normalized = normalized[len(word) :]
                stripped = True
                break
    return normalized
