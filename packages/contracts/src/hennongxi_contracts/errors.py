"""Sanitized structured errors safe for persistence and public responses."""

from __future__ import annotations

from enum import StrEnum

from hennongxi_contracts.common import ContractModel, NonBlankText, ShortText


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_PLAN = "INVALID_PLAN"
    TRANSITION_NOT_ALLOWED = "TRANSITION_NOT_ALLOWED"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    CONFLICT = "CONFLICT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    DATA_INVALID = "DATA_INVALID"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    QUALITY_FAILED = "QUALITY_FAILED"
    PUBLISHING_FAILED = "PUBLISHING_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(ContractModel):
    field: ShortText | None = None
    reason: NonBlankText


class StructuredError(ContractModel):
    code: ErrorCode
    message: NonBlankText
    retryable: bool
    details: tuple[ErrorDetail, ...] = ()


class ErrorResponse(ContractModel):
    error: StructuredError
