"""Atomic, idempotent publication for task-scoped quality reports."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from hennongxi_contracts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    QualityEvaluateCommand,
    QualityEvaluateResult,
    QualityMetrics,
)
from pydantic import ValidationError

_REPORT_FILENAME = "quality_report.json"
_RECEIPT_FILENAME = "quality_result.json"


class QualityArtifactConflictError(RuntimeError):
    """Raised when an attempt is already owned by another idempotency key."""


class QualityArtifactIntegrityError(RuntimeError):
    """Raised when a published quality report cannot be reverified."""


class QualityArtifactWriteSession:
    """One lock-held report attempt that is published once or safely reused."""

    def __init__(
        self,
        store: QualityArtifactStore,
        *,
        task_id: UUID,
        attempt: int,
        idempotency_key: UUID,
        staging_directory: Path | None,
        existing_result: QualityEvaluateResult | None,
    ) -> None:
        self._store = store
        self.task_id = task_id
        self.attempt = attempt
        self.idempotency_key = idempotency_key
        self._staging_directory = staging_directory
        self.existing_result = existing_result
        self._published = existing_result is not None

    @property
    def staging_directory(self) -> Path:
        if self._staging_directory is None:
            raise QualityArtifactConflictError("published quality attempt is not writable")
        return self._staging_directory

    def publish(
        self,
        command: QualityEvaluateCommand,
        metrics: QualityMetrics,
    ) -> QualityEvaluateResult:
        if self._published:
            raise QualityArtifactConflictError("quality report is already published")
        if command.task_id != self.task_id or command.attempt != self.attempt:
            raise QualityArtifactIntegrityError("quality command scope does not match its session")

        staging = self.staging_directory
        report_path = staging / _REPORT_FILENAME
        report_path.write_text(
            _canonical_json(_report_payload(command, metrics)) + "\n",
            encoding="utf-8",
        )
        _fsync_file(report_path)
        result = QualityEvaluateResult(
            task_id=command.task_id,
            step_id=command.step_id,
            attempt=command.attempt,
            correlation_id=command.correlation_id,
            metrics=metrics,
            artifact=ArtifactRef(
                artifact_id=uuid5(
                    command.task_id,
                    f"quality:{command.attempt}:{ArtifactType.QUALITY_REPORT.value}",
                ),
                task_id=command.task_id,
                attempt=command.attempt,
                artifact_type=ArtifactType.QUALITY_REPORT,
                status=ArtifactStatus.COMPLETE,
                media_type="application/json",
                created_at=datetime.now(UTC),
                checksum_sha256=_sha256(report_path),
                byte_size=report_path.stat().st_size,
            ),
        )
        receipt_path = staging / _RECEIPT_FILENAME
        receipt_path.write_text(
            _canonical_json(
                {
                    "idempotency_key": str(self.idempotency_key),
                    "result": result.model_dump(mode="json"),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _fsync_file(receipt_path)
        _fsync_directory(staging)

        final_directory = self._store.final_directory(self.task_id, self.attempt)
        os.replace(staging, final_directory)
        _fsync_directory(final_directory.parent)
        self._published = True
        return result

    def cleanup(self) -> None:
        if not self._published and self._staging_directory is not None:
            shutil.rmtree(self._staging_directory, ignore_errors=True)


class QualityArtifactStore:
    """Own fixed quality-report names beneath a dedicated writable root."""

    def __init__(self, report_root: Path) -> None:
        self._report_root = report_root

    def final_directory(self, task_id: UUID, attempt: int) -> Path:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        return self._report_root / str(task_id) / f"attempt-{attempt}" / "quality"

    def load_verified_result(self, task_id: UUID, attempt: int) -> QualityEvaluateResult:
        """Load one immutable prior result only after its receipt and report revalidate."""

        directory = self.final_directory(task_id, attempt)
        root = self._report_root.resolve()
        try:
            directory.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as error:
            raise QualityArtifactIntegrityError(
                "published quality report is outside report storage"
            ) from error
        task_directory = directory.parent.parent
        if any(path.is_symlink() for path in (task_directory, directory.parent, directory)):
            raise QualityArtifactIntegrityError(
                "published quality report directory cannot contain symlinks"
            )
        _, result = self._load_verified(directory, task_id, attempt)
        return result

    @contextmanager
    def session(
        self,
        task_id: UUID,
        attempt: int,
        idempotency_key: UUID,
    ) -> Iterator[QualityArtifactWriteSession]:
        final_directory = self.final_directory(task_id, attempt)
        attempt_directory = final_directory.parent
        attempt_directory.mkdir(parents=True, exist_ok=True)

        lock_path = attempt_directory / ".quality.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if final_directory.exists():
                stored_key, result = self._load_verified(final_directory, task_id, attempt)
                if stored_key != idempotency_key:
                    raise QualityArtifactConflictError(
                        "quality attempt was published with a different idempotency key"
                    )
                yield QualityArtifactWriteSession(
                    self,
                    task_id=task_id,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    staging_directory=None,
                    existing_result=result,
                )
                return

            for stale_path in attempt_directory.glob(".quality-staging-*"):
                if stale_path.is_dir() and not stale_path.is_symlink():
                    shutil.rmtree(stale_path)
                else:
                    stale_path.unlink(missing_ok=True)
            staging_directory = attempt_directory / f".quality-staging-{os.getpid()}"
            staging_directory.mkdir()
            session = QualityArtifactWriteSession(
                self,
                task_id=task_id,
                attempt=attempt,
                idempotency_key=idempotency_key,
                staging_directory=staging_directory,
                existing_result=None,
            )
            try:
                yield session
            finally:
                session.cleanup()

    def _load_verified(
        self,
        directory: Path,
        task_id: UUID,
        attempt: int,
    ) -> tuple[UUID, QualityEvaluateResult]:
        try:
            receipt_path = directory / _RECEIPT_FILENAME
            if receipt_path.is_symlink() or not receipt_path.is_file():
                raise ValueError("quality receipt must be a regular file")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or set(receipt) != {"idempotency_key", "result"}:
                raise ValueError("invalid quality receipt shape")
            idempotency_key = UUID(str(receipt["idempotency_key"]))
            result = QualityEvaluateResult.model_validate(receipt["result"])
            if result.task_id != task_id or result.attempt != attempt:
                raise ValueError("quality result scope does not match its directory")
            report_path = directory / _REPORT_FILENAME
            if (
                report_path.is_symlink()
                or not report_path.is_file()
                or report_path.stat().st_size != result.artifact.byte_size
                or _sha256(report_path) != result.artifact.checksum_sha256
            ):
                raise ValueError("quality report metadata does not match its content")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report != _report_payload(result, result.metrics):
                raise ValueError("quality report content does not match its result")
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise QualityArtifactIntegrityError("published quality report is invalid") from error
        return idempotency_key, result


def _report_payload(
    scope: QualityEvaluateCommand | QualityEvaluateResult,
    metrics: QualityMetrics,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_id": str(scope.task_id),
        "step_id": scope.step_id,
        "attempt": scope.attempt,
        "correlation_id": str(scope.correlation_id),
        "metrics": metrics.model_dump(mode="json"),
    }


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
