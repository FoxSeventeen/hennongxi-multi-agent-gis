"""Task-scoped, checksum-verified atomic publication for analysis artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid5

from hennongxi_contracts import (
    AnalysisRunResult,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
)
from pydantic import ValidationError

ANALYSIS_ARTIFACT_TYPES: Final[tuple[ArtifactType, ...]] = (
    ArtifactType.NDVI_BEFORE,
    ArtifactType.NDVI_AFTER,
    ArtifactType.NDVI_DIFFERENCE,
    ArtifactType.CHANGE_CLASSIFICATION,
    ArtifactType.AREA_STATISTICS,
)
_FILENAMES: Final[dict[ArtifactType, str]] = {
    artifact_type: (
        f"{artifact_type.value.lower()}.json"
        if artifact_type is ArtifactType.AREA_STATISTICS
        else f"{artifact_type.value.lower()}.tif"
    )
    for artifact_type in ANALYSIS_ARTIFACT_TYPES
}
_MEDIA_TYPES: Final[dict[ArtifactType, str]] = {
    artifact_type: (
        "application/json"
        if artifact_type is ArtifactType.AREA_STATISTICS
        else "image/tiff; application=geotiff"
    )
    for artifact_type in ANALYSIS_ARTIFACT_TYPES
}
_RECEIPT_FILENAME = "analysis_result.json"


class ArtifactConflictError(RuntimeError):
    """Raised when another idempotency key already owns the task attempt."""


class ArtifactIntegrityError(RuntimeError):
    """Raised when a staged or published artifact fails integrity validation."""


class ArtifactWriteSession:
    """One lock-held attempt that either reuses or atomically publishes a result."""

    def __init__(
        self,
        store: AnalysisArtifactStore,
        *,
        task_id: UUID,
        attempt: int,
        idempotency_key: UUID,
        staging_directory: Path | None,
        existing_result: AnalysisRunResult | None,
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
            raise ArtifactConflictError("published attempt has no writable staging directory")
        return self._staging_directory

    def path_for(self, artifact_type: ArtifactType) -> Path:
        try:
            filename = _FILENAMES[artifact_type]
        except KeyError as error:
            raise ValueError("unsupported analysis artifact type") from error
        return self.staging_directory / filename

    def publish(self, result: AnalysisRunResult) -> None:
        if self._published:
            raise ArtifactConflictError("analysis result is already published")

        staging = self.staging_directory
        self._store._verify_result(result, staging, self.task_id, self.attempt)
        for filename in _FILENAMES.values():
            _fsync_file(staging / filename)
        receipt = {
            "idempotency_key": str(self.idempotency_key),
            "result": result.model_dump(mode="json"),
        }
        receipt_path = staging / _RECEIPT_FILENAME
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        _fsync_file(receipt_path)
        _fsync_directory(staging)

        final_directory = self._store.final_directory(self.task_id, self.attempt)
        os.replace(staging, final_directory)
        _fsync_directory(final_directory.parent)
        self._published = True

    def cleanup(self) -> None:
        if not self._published and self._staging_directory is not None:
            shutil.rmtree(self._staging_directory, ignore_errors=True)


class AnalysisArtifactStore:
    """Own fixed output names beneath a validated task/attempt directory."""

    def __init__(self, artifact_root: Path) -> None:
        self._artifact_root = artifact_root

    def final_directory(self, task_id: UUID, attempt: int) -> Path:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        return self._artifact_root / str(task_id) / f"attempt-{attempt}" / "analysis"

    @contextmanager
    def session(
        self,
        task_id: UUID,
        attempt: int,
        idempotency_key: UUID,
    ) -> Iterator[ArtifactWriteSession]:
        final_directory = self.final_directory(task_id, attempt)
        attempt_directory = final_directory.parent
        attempt_directory.mkdir(parents=True, exist_ok=True)

        lock_path = attempt_directory / ".analysis.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if final_directory.exists():
                stored_key, result = self._load_verified(final_directory, task_id, attempt)
                if stored_key != idempotency_key:
                    raise ArtifactConflictError(
                        "analysis attempt was published with a different idempotency key"
                    )
                yield ArtifactWriteSession(
                    self,
                    task_id=task_id,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    staging_directory=None,
                    existing_result=result,
                )
                return

            for stale_path in attempt_directory.glob(".analysis-staging-*"):
                if stale_path.is_dir() and not stale_path.is_symlink():
                    shutil.rmtree(stale_path)
                else:
                    stale_path.unlink(missing_ok=True)
            staging_directory = attempt_directory / f".analysis-staging-{os.getpid()}"
            staging_directory.mkdir()
            session = ArtifactWriteSession(
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

    def artifact_ref(
        self,
        directory: Path,
        *,
        task_id: UUID,
        attempt: int,
        artifact_type: ArtifactType,
        created_at: datetime,
    ) -> ArtifactRef:
        path = directory / _FILENAMES[artifact_type]
        if not path.is_file() or path.stat().st_size <= 0:
            raise ArtifactIntegrityError("analysis artifact is missing or empty")
        return ArtifactRef(
            artifact_id=uuid5(task_id, f"analysis:{attempt}:{artifact_type.value}"),
            task_id=task_id,
            attempt=attempt,
            artifact_type=artifact_type,
            status=ArtifactStatus.COMPLETE,
            media_type=_MEDIA_TYPES[artifact_type],
            created_at=created_at,
            checksum_sha256=_sha256(path),
            byte_size=path.stat().st_size,
        )

    def _load_verified(
        self,
        directory: Path,
        task_id: UUID,
        attempt: int,
    ) -> tuple[UUID, AnalysisRunResult]:
        try:
            payload = json.loads((directory / _RECEIPT_FILENAME).read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"idempotency_key", "result"}:
                raise ValueError("invalid receipt shape")
            idempotency_key = UUID(str(payload["idempotency_key"]))
            result = AnalysisRunResult.model_validate(payload["result"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError) as error:
            raise ArtifactIntegrityError("published analysis receipt is invalid") from error

        self._verify_result(result, directory, task_id, attempt)
        return idempotency_key, result

    def _verify_result(
        self,
        result: AnalysisRunResult,
        directory: Path,
        task_id: UUID,
        attempt: int,
    ) -> None:
        if result.task_id != task_id or result.attempt != attempt:
            raise ArtifactIntegrityError("analysis result scope does not match its directory")

        refs = {artifact.artifact_type: artifact for artifact in result.artifacts}
        for artifact_type in ANALYSIS_ARTIFACT_TYPES:
            path = directory / _FILENAMES[artifact_type]
            artifact = refs[artifact_type]
            if not path.is_file() or path.stat().st_size != artifact.byte_size:
                raise ArtifactIntegrityError("analysis artifact size does not match its metadata")
            if _sha256(path) != artifact.checksum_sha256:
                raise ArtifactIntegrityError(
                    "analysis artifact checksum does not match its metadata"
                )


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
