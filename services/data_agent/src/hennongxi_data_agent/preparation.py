"""Path-free Data Agent preparation service over the approved local manifest."""

from __future__ import annotations

from pathlib import Path

from hennongxi_contracts import (
    DataPrepareCommand,
    DataPrepareResult,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    StructuredError,
)

from hennongxi_data_agent.dataset import ManifestValidationError, run_preflight


class DataPreparationFailure(RuntimeError):
    """Carry one sanitized contract response from the service to the HTTP adapter."""

    def __init__(self, status_code: int, response: ErrorResponse) -> None:
        super().__init__(response.error.code.value)
        self.status_code = status_code
        self.response = response


class DataPreparer:
    """Resolve only manifest-owned logical IDs and return verified, path-free metadata."""

    def __init__(self, manifest_path: Path, *, data_root: Path, cache_dir: Path) -> None:
        self._manifest_path = manifest_path
        self._data_root = data_root
        self._cache_dir = cache_dir

    def prepare(self, command: DataPrepareCommand) -> DataPrepareResult:
        try:
            report = run_preflight(
                self._manifest_path,
                data_root=self._data_root,
                cache_dir=self._cache_dir,
            )
        except ManifestValidationError as error:
            raise DataPreparationFailure(
                503,
                ErrorResponse(
                    error=StructuredError(
                        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                        message="approved data manifest is unavailable",
                        retryable=True,
                    )
                ),
            ) from error

        if not report.ok:
            details = tuple(
                ErrorDetail(
                    field=f"{check.logical_id}.{check.name}",
                    reason=check.message,
                )
                for check in report.checks
                if not check.ok
            )
            raise DataPreparationFailure(
                409,
                ErrorResponse(
                    error=StructuredError(
                        code=ErrorCode.DATA_INVALID,
                        message="approved local dataset failed validation",
                        retryable=True,
                        details=details,
                    )
                ),
            )

        return DataPrepareResult(
            task_id=command.task_id,
            step_id=command.step_id,
            attempt=command.attempt,
            correlation_id=command.correlation_id,
            assets=report.assets,
        )
