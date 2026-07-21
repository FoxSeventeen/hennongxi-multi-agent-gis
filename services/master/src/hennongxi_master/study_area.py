"""Provider-neutral study-area intent and evidence primitives."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, Self

from hennongxi_contracts.common import UtcDateTime
from pydantic import BaseModel, ConfigDict, Field, model_validator


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
