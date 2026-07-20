"""Atomic, idempotent storage for task-bound Publisher PDF reports."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from hennongxi_contracts import ArtifactRef, ArtifactStatus, ArtifactType
from pydantic import ValidationError

_REPORT_FILENAME = "report.pdf"
_RECEIPT_FILENAME = "report_result.json"
_ATTEMPT_PATTERN = re.compile(r"^attempt-([1-9][0-9]*)$")


class ReportArtifactConflictError(RuntimeError):
    """Raised when another idempotency key already owns the Publisher attempt."""


class ReportArtifactIntegrityError(RuntimeError):
    """Raised when a staged or published report cannot be verified."""


class ReportArtifactNotFoundError(LookupError):
    """Raised when a task does not own the requested report artifact."""


@dataclass(frozen=True, slots=True)
class ReportArtifactOutcome:
    artifact: ArtifactRef
    path: Path
    reused: bool


@dataclass(frozen=True, slots=True)
class ReportArtifactDownload:
    artifact: ArtifactRef
    payload: bytes


class ReportArtifactStore:
    """Publish one fixed PDF and receipt below a task/attempt Publisher directory."""

    def __init__(self, artifact_root: Path) -> None:
        self._artifact_root = artifact_root

    def final_directory(self, task_id: UUID, attempt: int) -> Path:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        return self._artifact_root / str(task_id) / f"attempt-{attempt}" / "publisher"

    def read(self, task_id: UUID, artifact_id: UUID) -> ReportArtifactDownload:
        task_directory = self._artifact_root / str(task_id)
        if not task_directory.exists():
            raise ReportArtifactNotFoundError("task-bound PDF report was not found")
        _require_real_directory(self._artifact_root)
        _require_real_directory(task_directory)
        try:
            candidates = sorted(
                (
                    (int(match.group(1)), path)
                    for path in task_directory.iterdir()
                    if (match := _ATTEMPT_PATTERN.fullmatch(path.name)) is not None
                ),
                reverse=True,
            )
        except OSError as error:
            raise ReportArtifactIntegrityError("published PDF report is invalid") from error

        for attempt, attempt_directory in candidates:
            _require_real_directory(attempt_directory)
            directory = attempt_directory / "publisher"
            if directory.is_symlink():
                raise ReportArtifactIntegrityError("published PDF report is invalid")
            if not directory.exists():
                continue
            _stored_key, artifact = self._load_verified(directory, task_id, attempt)
            if artifact.artifact_id != artifact_id:
                continue
            try:
                payload = (directory / _REPORT_FILENAME).read_bytes()
            except OSError as error:
                raise ReportArtifactIntegrityError("published PDF report is invalid") from error
            if (
                not _looks_like_pdf(payload)
                or len(payload) != artifact.byte_size
                or hashlib.sha256(payload).hexdigest() != artifact.checksum_sha256
            ):
                raise ReportArtifactIntegrityError("published PDF report is invalid")
            return ReportArtifactDownload(artifact=artifact, payload=payload)

        raise ReportArtifactNotFoundError("task-bound PDF report was not found")

    def publish(
        self,
        *,
        task_id: UUID,
        attempt: int,
        idempotency_key: UUID,
        created_at: datetime,
        payload: bytes,
    ) -> ReportArtifactOutcome:
        if not _looks_like_pdf(payload):
            raise ReportArtifactIntegrityError("report payload must contain valid PDF bytes")

        attempt_directory = self._prepare_attempt_directory(task_id, attempt)
        final_directory = self.final_directory(task_id, attempt)
        lock_path = attempt_directory / ".publisher.lock"
        if lock_path.is_symlink():
            raise ReportArtifactIntegrityError("publisher lock must not be a symbolic link")

        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        with os.fdopen(descriptor, "a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if final_directory.is_symlink():
                raise ReportArtifactIntegrityError("published PDF report is invalid")
            if final_directory.exists():
                stored_key, artifact = self._load_verified(
                    final_directory,
                    task_id,
                    attempt,
                )
                if stored_key != idempotency_key:
                    raise ReportArtifactConflictError(
                        "publisher attempt was published with a different idempotency key"
                    )
                return ReportArtifactOutcome(
                    artifact=artifact,
                    path=final_directory / _REPORT_FILENAME,
                    reused=True,
                )

            self._remove_stale_staging(attempt_directory)
            staging_directory = attempt_directory / f".publisher-staging-{os.getpid()}"
            staging_directory.mkdir(mode=0o700)
            try:
                artifact = self._write_staging(
                    staging_directory,
                    task_id=task_id,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    created_at=created_at,
                    payload=payload,
                )
                os.replace(staging_directory, final_directory)
                _fsync_directory(attempt_directory)
            finally:
                if staging_directory.exists() and not staging_directory.is_symlink():
                    shutil.rmtree(staging_directory, ignore_errors=True)

        return ReportArtifactOutcome(
            artifact=artifact,
            path=final_directory / _REPORT_FILENAME,
            reused=False,
        )

    def _prepare_attempt_directory(self, task_id: UUID, attempt: int) -> Path:
        final_directory = self.final_directory(task_id, attempt)
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        _require_real_directory(self._artifact_root)
        task_directory = self._artifact_root / str(task_id)
        task_directory.mkdir(exist_ok=True)
        _require_real_directory(task_directory)
        attempt_directory = final_directory.parent
        attempt_directory.mkdir(exist_ok=True)
        _require_real_directory(attempt_directory)
        return attempt_directory

    @staticmethod
    def _remove_stale_staging(attempt_directory: Path) -> None:
        for stale_path in attempt_directory.glob(".publisher-staging-*"):
            if stale_path.is_dir() and not stale_path.is_symlink():
                shutil.rmtree(stale_path)
            else:
                stale_path.unlink(missing_ok=True)

    @staticmethod
    def _write_staging(
        staging_directory: Path,
        *,
        task_id: UUID,
        attempt: int,
        idempotency_key: UUID,
        created_at: datetime,
        payload: bytes,
    ) -> ArtifactRef:
        report_path = staging_directory / _REPORT_FILENAME
        report_path.write_bytes(payload)
        _fsync_file(report_path)
        artifact = ArtifactRef(
            artifact_id=uuid5(task_id, f"publisher:{attempt}:{ArtifactType.PDF_REPORT.value}"),
            task_id=task_id,
            attempt=attempt,
            artifact_type=ArtifactType.PDF_REPORT,
            status=ArtifactStatus.COMPLETE,
            media_type="application/pdf",
            created_at=created_at,
            checksum_sha256=_sha256(report_path),
            byte_size=report_path.stat().st_size,
        )
        receipt_path = staging_directory / _RECEIPT_FILENAME
        receipt_path.write_text(
            _canonical_json(
                {
                    "idempotency_key": str(idempotency_key),
                    "artifact": artifact.model_dump(mode="json"),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _fsync_file(receipt_path)
        _fsync_directory(staging_directory)
        return artifact

    @staticmethod
    def _load_verified(
        directory: Path,
        task_id: UUID,
        attempt: int,
    ) -> tuple[UUID, ArtifactRef]:
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("publisher directory must be a real directory")
            if {path.name for path in directory.iterdir()} != {
                _REPORT_FILENAME,
                _RECEIPT_FILENAME,
            }:
                raise ValueError("publisher directory has an invalid shape")
            receipt_path = directory / _RECEIPT_FILENAME
            if receipt_path.is_symlink() or not receipt_path.is_file():
                raise ValueError("publisher receipt must be a regular file")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or set(receipt) != {
                "idempotency_key",
                "artifact",
            }:
                raise ValueError("publisher receipt has an invalid shape")
            idempotency_key = UUID(str(receipt["idempotency_key"]))
            artifact = ArtifactRef.model_validate(receipt["artifact"])
            expected_id = uuid5(task_id, f"publisher:{attempt}:{ArtifactType.PDF_REPORT.value}")
            if (
                artifact.artifact_id != expected_id
                or artifact.task_id != task_id
                or artifact.attempt != attempt
                or artifact.artifact_type is not ArtifactType.PDF_REPORT
                or artifact.status is not ArtifactStatus.COMPLETE
                or artifact.media_type != "application/pdf"
            ):
                raise ValueError("publisher artifact scope is invalid")
            report_path = directory / _REPORT_FILENAME
            if (
                report_path.is_symlink()
                or not report_path.is_file()
                or not _file_looks_like_pdf(report_path)
                or report_path.stat().st_size != artifact.byte_size
                or _sha256(report_path) != artifact.checksum_sha256
            ):
                raise ValueError("publisher report does not match its receipt")
        except (
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
        ) as error:
            raise ReportArtifactIntegrityError("published PDF report is invalid") from error
        return idempotency_key, artifact


def _looks_like_pdf(payload: bytes) -> bool:
    return (
        len(payload) > 16 and payload.startswith(b"%PDF-") and payload.rstrip().endswith(b"%%EOF")
    )


def _file_looks_like_pdf(path: Path) -> bool:
    with path.open("rb") as stream:
        start = stream.read(5)
        stream.seek(max(0, path.stat().st_size - 16))
        end = stream.read()
    return start == b"%PDF-" and end.rstrip().endswith(b"%%EOF")


def _require_real_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ReportArtifactIntegrityError("publisher storage path must be a real directory")


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
