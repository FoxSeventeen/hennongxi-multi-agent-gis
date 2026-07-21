from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from hennongxi_contracts import ErrorCode, ErrorResponse
from hennongxi_master.amap import AmapVerification, AmapVerificationCode
from hennongxi_master.main import create_master_app
from hennongxi_master.study_area import (
    StudyAreaConclusion,
    StudyAreaEvidence,
    StudyAreaGrounder,
    StudyAreaIntent,
    StudyAreaReasonCode,
    resolve_study_area,
)
from pydantic import ValidationError

CHECKED_AT = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


class _OnlineVerifier:
    def __init__(self, result: AmapVerification | Exception) -> None:
        self.result = result
        self.calls = 0

    async def verify(self) -> AmapVerification:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    "query",
    [
        "监测神农溪流域两期 NDVI 变化",
        "分析神农溪景区生态变化",
        "评估巴东县植被变化",
        "巴东生态变化监测",
    ],
)
def test_resolver_accepts_only_approved_study_area_aliases(query: str) -> None:
    assert resolve_study_area(query) is StudyAreaIntent.APPROVED


@pytest.mark.parametrize(
    "query",
    [
        "分析武汉市东湖植被变化",
        "监测长江流域两期 NDVI 变化",
        "北京市植被变化",
        "对比神农溪流域与武汉市东湖的生态变化",
    ],
)
def test_resolver_rejects_clear_out_of_scope_locations(query: str) -> None:
    assert resolve_study_area(query) is StudyAreaIntent.OUT_OF_SCOPE


@pytest.mark.parametrize(
    "query",
    [
        "分析植被变化",
        "请生成生态监测报告",
        "分析上市公司环境报告",
    ],
)
def test_resolver_keeps_queries_without_a_clear_location_ambiguous(query: str) -> None:
    assert resolve_study_area(query) is StudyAreaIntent.AMBIGUOUS


@pytest.mark.parametrize(
    ("conclusion", "reason_code", "retryable"),
    [
        (
            StudyAreaConclusion.VERIFIED,
            StudyAreaReasonCode.ONLINE_MATCH_CONFIRMED,
            False,
        ),
        (
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_CHECK_UNAVAILABLE,
            True,
        ),
        (
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.LOCAL_STUDY_AREA_AMBIGUOUS,
            False,
        ),
        (
            StudyAreaConclusion.REJECTED,
            StudyAreaReasonCode.OUT_OF_SCOPE_STUDY_AREA,
            False,
        ),
    ],
)
def test_study_area_evidence_contains_only_provider_neutral_fields(
    conclusion: StudyAreaConclusion,
    reason_code: StudyAreaReasonCode,
    retryable: bool,
) -> None:
    evidence = StudyAreaEvidence(
        conclusion=conclusion,
        checked_at=CHECKED_AT,
        duration_ms=12,
        reason_code=reason_code,
        retryable=retryable,
    )

    assert evidence.model_dump(mode="json") == {
        "conclusion": conclusion.value,
        "checked_at": "2026-07-21T12:00:00Z",
        "duration_ms": 12,
        "reason_code": reason_code.value,
        "retryable": retryable,
    }


@pytest.mark.parametrize(
    ("conclusion", "reason_code", "retryable"),
    [
        (
            StudyAreaConclusion.VERIFIED,
            StudyAreaReasonCode.ONLINE_CHECK_UNAVAILABLE,
            True,
        ),
        (
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_MATCH_CONFIRMED,
            False,
        ),
        (
            StudyAreaConclusion.REJECTED,
            StudyAreaReasonCode.LOCAL_STUDY_AREA_AMBIGUOUS,
            False,
        ),
        (
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_CHECK_UNAVAILABLE,
            False,
        ),
    ],
)
def test_study_area_evidence_rejects_inconsistent_conclusions(
    conclusion: StudyAreaConclusion,
    reason_code: StudyAreaReasonCode,
    retryable: bool,
) -> None:
    with pytest.raises(ValidationError):
        StudyAreaEvidence(
            conclusion=conclusion,
            checked_at=CHECKED_AT,
            duration_ms=0,
            reason_code=reason_code,
            retryable=retryable,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "provider_code",
        "match_count",
        "provider_retryable",
        "expected_conclusion",
        "expected_reason",
    ),
    [
        (
            AmapVerificationCode.VERIFIED,
            1,
            False,
            StudyAreaConclusion.VERIFIED,
            StudyAreaReasonCode.ONLINE_MATCH_CONFIRMED,
        ),
        (
            AmapVerificationCode.NO_MATCH,
            0,
            False,
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_NO_MATCH,
        ),
        (
            AmapVerificationCode.AMBIGUOUS,
            2,
            False,
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_MATCH_AMBIGUOUS,
        ),
        (
            AmapVerificationCode.AUTHENTICATION_FAILED,
            0,
            False,
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_AUTHENTICATION_FAILED,
        ),
        (
            AmapVerificationCode.QUOTA_EXCEEDED,
            0,
            True,
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_QUOTA_EXCEEDED,
        ),
        (
            AmapVerificationCode.RATE_LIMITED,
            0,
            True,
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_RATE_LIMITED,
        ),
        (
            AmapVerificationCode.PROVIDER_UNAVAILABLE,
            0,
            True,
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_CHECK_UNAVAILABLE,
        ),
        (
            AmapVerificationCode.REQUEST_REJECTED,
            0,
            False,
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_REQUEST_REJECTED,
        ),
        (
            AmapVerificationCode.RESPONSE_INVALID,
            0,
            True,
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.ONLINE_RESPONSE_INVALID,
        ),
    ],
)
async def test_grounder_maps_online_results_to_provider_neutral_evidence(
    provider_code: AmapVerificationCode,
    match_count: int,
    provider_retryable: bool,
    expected_conclusion: StudyAreaConclusion,
    expected_reason: StudyAreaReasonCode,
) -> None:
    verifier = _OnlineVerifier(
        AmapVerification(
            code=provider_code,
            checked_at=CHECKED_AT,
            duration_ms=12,
            retryable=provider_retryable,
            match_count=match_count,
        )
    )

    evidence = await StudyAreaGrounder(verifier).verify_query("分析神农溪流域变化")

    assert evidence.conclusion is expected_conclusion
    assert evidence.reason_code is expected_reason
    assert evidence.checked_at == CHECKED_AT
    assert evidence.duration_ms == 12
    assert evidence.retryable is provider_retryable
    assert verifier.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_conclusion", "expected_reason"),
    [
        (
            "分析植被变化",
            StudyAreaConclusion.DEGRADED,
            StudyAreaReasonCode.LOCAL_STUDY_AREA_AMBIGUOUS,
        ),
        (
            "分析武汉市东湖植被变化",
            StudyAreaConclusion.REJECTED,
            StudyAreaReasonCode.OUT_OF_SCOPE_STUDY_AREA,
        ),
    ],
)
async def test_grounder_never_calls_online_service_for_unapproved_local_intent(
    query: str,
    expected_conclusion: StudyAreaConclusion,
    expected_reason: StudyAreaReasonCode,
) -> None:
    verifier = _OnlineVerifier(AssertionError("online verifier must not be called"))

    evidence = await StudyAreaGrounder(verifier, now=lambda: CHECKED_AT).verify_query(query)

    assert evidence.conclusion is expected_conclusion
    assert evidence.reason_code is expected_reason
    assert evidence.duration_ms == 0
    assert verifier.calls == 0


@pytest.mark.asyncio
async def test_grounder_degrades_safely_when_online_check_is_unconfigured() -> None:
    evidence = await StudyAreaGrounder(None, now=lambda: CHECKED_AT).verify_query(
        "分析神农溪流域变化"
    )

    assert evidence == StudyAreaEvidence(
        conclusion=StudyAreaConclusion.DEGRADED,
        checked_at=CHECKED_AT,
        duration_ms=0,
        reason_code=StudyAreaReasonCode.ONLINE_CHECK_NOT_CONFIGURED,
        retryable=False,
    )


@pytest.mark.asyncio
async def test_grounder_contains_unexpected_online_failures_without_provider_details() -> None:
    private_detail = "private-provider-payload"
    verifier = _OnlineVerifier(RuntimeError(private_detail))

    evidence = await StudyAreaGrounder(verifier, now=lambda: CHECKED_AT).verify_query(
        "分析神农溪流域变化"
    )

    serialized = evidence.model_dump_json()
    assert evidence.reason_code is StudyAreaReasonCode.ONLINE_CHECK_UNAVAILABLE
    assert evidence.retryable is True
    assert private_detail not in serialized


def test_task_api_rejects_clear_out_of_scope_location_before_repository_access() -> None:
    master = create_master_app({"ORCHESTRATION_WORKER_ENABLED": "false"})
    repository_factory_calls: list[None] = []
    master.state.task_repository_factory = lambda: repository_factory_calls.append(None)

    with TestClient(master) as client:
        response = client.post(
            "/api/v1/tasks",
            json={"query": "分析武汉市东湖植被变化"},
        )

    assert response.status_code == 422
    error = ErrorResponse.model_validate(response.json()).error
    assert error.code is ErrorCode.VALIDATION_ERROR
    assert error.message == "目前仅支持神农溪流域生态变化监测"
    assert error.retryable is False
    assert repository_factory_calls == []
