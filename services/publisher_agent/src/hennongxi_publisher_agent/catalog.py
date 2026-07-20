"""Read-only discovery of checksum-verified, quality-passed tile artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from uuid import UUID

from hennongxi_contracts import (
    AnalysisRunResult,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    PublisherPublishCommand,
    QualityConclusion,
    QualityEvaluateResult,
    TileArtifactType,
)
from pydantic import ValidationError

_ATTEMPT_PATTERN = re.compile(r"^attempt-([1-9][0-9]*)$")
_ANALYSIS_RECEIPT = "analysis_result.json"
_QUALITY_RECEIPT = "quality_result.json"
_QUALITY_REPORT = "quality_report.json"
_ANALYSIS_FILENAMES = {
    ArtifactType.NDVI_BEFORE: "ndvi_before.tif",
    ArtifactType.NDVI_AFTER: "ndvi_after.tif",
    ArtifactType.NDVI_DIFFERENCE: "ndvi_difference.tif",
    ArtifactType.CHANGE_CLASSIFICATION: "change_classification.tif",
    ArtifactType.AREA_STATISTICS: "area_statistics.json",
}
_GEOTIFF_MEDIA_TYPE = "image/tiff; application=geotiff"


class PublishedTileNotFoundError(LookupError):
    """Raised when no quality-passed attempt owns the requested tile artifact."""


class PublishedTileIntegrityError(RuntimeError):
    """Raised when published receipts or files no longer agree."""


@dataclass(frozen=True, slots=True)
class _FileFingerprint:
    path: Path
    root: Path
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class ResolvedTile:
    path: Path
    artifact: ArtifactRef
    attempt: int


@dataclass(frozen=True, slots=True)
class ResolvedPublication:
    attempt: int
    correlation_id: UUID
    tiles: tuple[ResolvedTile, ...]


@dataclass(frozen=True, slots=True)
class _VerifiedAttempt:
    attempt: int
    paths: dict[TileArtifactType, Path]
    artifacts: dict[TileArtifactType, ArtifactRef]
    input_artifacts: tuple[ArtifactRef, ...]
    quality: QualityEvaluateResult
    _fingerprints: tuple[_FileFingerprint, ...] = field(
        repr=False,
        compare=False,
    )


class PublisherArtifactCatalog:
    """Resolve fixed artifacts without accepting storage paths from HTTP callers."""

    def __init__(self, analysis_root: Path, quality_root: Path) -> None:
        self._analysis_root = analysis_root
        self._quality_root = quality_root
        self._cache: dict[UUID, _VerifiedAttempt] = {}
        self._lock = Lock()

    def resolve_tile(self, task_id: UUID, artifact_type: TileArtifactType) -> ResolvedTile:
        with self._lock:
            verified = self._verified_attempt(task_id)
            return _resolved_tile(verified, artifact_type)

    def resolve_publication(self, command: PublisherPublishCommand) -> ResolvedPublication:
        with self._lock:
            verified = self._verified_attempt(command.task_id)
            expected_artifacts = {
                artifact.artifact_type: artifact for artifact in verified.input_artifacts
            }
            supplied_artifacts = {
                artifact.artifact_type: artifact for artifact in command.artifacts
            }
            if (
                verified.attempt != command.attempt
                or verified.quality.correlation_id != command.correlation_id
                or verified.quality.metrics != command.quality
                or supplied_artifacts != expected_artifacts
            ):
                raise PublishedTileIntegrityError("published tile integrity validation failed")
            return ResolvedPublication(
                attempt=verified.attempt,
                correlation_id=verified.quality.correlation_id,
                tiles=tuple(
                    _resolved_tile(verified, artifact_type) for artifact_type in TileArtifactType
                ),
            )

    def _verified_attempt(self, task_id: UUID) -> _VerifiedAttempt:
        cached = self._cache.get(task_id)
        if cached is not None and _fingerprints_are_unchanged(cached._fingerprints):
            return cached
        self._cache.pop(task_id, None)
        verified = self._resolve_uncached(task_id)
        self._cache[task_id] = verified
        return verified

    def _resolve_uncached(self, task_id: UUID) -> _VerifiedAttempt:
        task_directory = self._analysis_root / str(task_id)
        if not _safe_directory(task_directory, self._analysis_root, required=False):
            raise PublishedTileNotFoundError("published tile was not found")

        attempts: list[tuple[int, Path]] = []
        try:
            for candidate in task_directory.iterdir():
                match = _ATTEMPT_PATTERN.fullmatch(candidate.name)
                if match is None:
                    continue
                if not _safe_directory(candidate, self._analysis_root, required=True):
                    continue
                attempts.append((int(match.group(1)), candidate))
        except OSError as error:
            raise PublishedTileIntegrityError(
                "published tile integrity validation failed"
            ) from error

        for attempt, attempt_directory in sorted(attempts, reverse=True):
            quality_directory = self._quality_root / str(task_id) / f"attempt-{attempt}" / "quality"
            if not _safe_directory(quality_directory, self._quality_root, required=False):
                continue
            quality, quality_fingerprints = self._load_quality(
                quality_directory,
                task_id,
                attempt,
            )
            if (
                quality.metrics.conclusion is not QualityConclusion.PASS
                or not quality.metrics.passed
            ):
                continue

            analysis_directory = attempt_directory / "analysis"
            if not _safe_directory(analysis_directory, self._analysis_root, required=True):
                raise PublishedTileIntegrityError("published tile integrity validation failed")
            analysis, receipt_fingerprint = _load_analysis_receipt(
                analysis_directory / _ANALYSIS_RECEIPT,
                self._analysis_root,
            )
            if (
                analysis.task_id != task_id
                or analysis.attempt != attempt
                or analysis.correlation_id != quality.correlation_id
            ):
                raise PublishedTileIntegrityError("published tile integrity validation failed")

            refs = {artifact.artifact_type: artifact for artifact in analysis.artifacts}
            paths: dict[TileArtifactType, Path] = {}
            artifacts: dict[TileArtifactType, ArtifactRef] = {}
            analysis_fingerprints = [receipt_fingerprint]
            for expected_type, filename in _ANALYSIS_FILENAMES.items():
                artifact = refs.get(expected_type)
                expected_media_type = (
                    "application/json"
                    if expected_type is ArtifactType.AREA_STATISTICS
                    else _GEOTIFF_MEDIA_TYPE
                )
                if (
                    artifact is None
                    or artifact.status is not ArtifactStatus.COMPLETE
                    or artifact.media_type != expected_media_type
                ):
                    raise PublishedTileIntegrityError("published tile integrity validation failed")
                path = analysis_directory / filename
                analysis_fingerprints.append(
                    _verify_file(
                        path,
                        self._analysis_root,
                        expected_size=artifact.byte_size,
                        expected_sha256=artifact.checksum_sha256,
                    )
                )
                if expected_type is not ArtifactType.AREA_STATISTICS:
                    tile_type = TileArtifactType(expected_type.value)
                    paths[tile_type] = path
                    artifacts[tile_type] = artifact

            return _VerifiedAttempt(
                attempt=attempt,
                paths=paths,
                artifacts=artifacts,
                input_artifacts=(*analysis.artifacts, quality.artifact),
                quality=quality,
                _fingerprints=(
                    *analysis_fingerprints,
                    *quality_fingerprints,
                ),
            )

        raise PublishedTileNotFoundError("published tile was not found")

    def _load_quality(
        self,
        directory: Path,
        task_id: UUID,
        attempt: int,
    ) -> tuple[QualityEvaluateResult, tuple[_FileFingerprint, _FileFingerprint]]:
        result, receipt_fingerprint = _load_quality_receipt(
            directory / _QUALITY_RECEIPT,
            self._quality_root,
        )
        if result.task_id != task_id or result.attempt != attempt:
            raise PublishedTileIntegrityError("published tile integrity validation failed")
        report_path = directory / _QUALITY_REPORT
        report_fingerprint = _verify_file(
            report_path,
            self._quality_root,
            expected_size=result.artifact.byte_size,
            expected_sha256=result.artifact.checksum_sha256,
        )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            expected_report = {
                "schema_version": "1.0",
                "task_id": str(result.task_id),
                "step_id": result.step_id,
                "attempt": result.attempt,
                "correlation_id": str(result.correlation_id),
                "metrics": result.metrics.model_dump(mode="json"),
            }
            if report != expected_report:
                raise ValueError("quality report does not match its receipt")
            if _fingerprint(report_path, self._quality_root) != report_fingerprint:
                raise ValueError("quality report changed while it was being verified")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise PublishedTileIntegrityError(
                "published tile integrity validation failed"
            ) from error
        return result, (receipt_fingerprint, report_fingerprint)


def _resolved_tile(
    verified: _VerifiedAttempt,
    artifact_type: TileArtifactType,
) -> ResolvedTile:
    return ResolvedTile(
        path=verified.paths[artifact_type],
        artifact=verified.artifacts[artifact_type],
        attempt=verified.attempt,
    )


def _load_analysis_receipt(
    path: Path,
    root: Path,
) -> tuple[AnalysisRunResult, _FileFingerprint]:
    payload, fingerprint = _load_receipt(path, root)
    try:
        return AnalysisRunResult.model_validate(payload["result"]), fingerprint
    except (KeyError, TypeError, ValidationError) as error:
        raise PublishedTileIntegrityError("published tile integrity validation failed") from error


def _load_quality_receipt(
    path: Path,
    root: Path,
) -> tuple[QualityEvaluateResult, _FileFingerprint]:
    payload, fingerprint = _load_receipt(path, root)
    try:
        return QualityEvaluateResult.model_validate(payload["result"]), fingerprint
    except (KeyError, TypeError, ValidationError) as error:
        raise PublishedTileIntegrityError("published tile integrity validation failed") from error


def _load_receipt(path: Path, root: Path) -> tuple[dict[str, object], _FileFingerprint]:
    fingerprint = _verified_regular_file(path, root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"idempotency_key", "result"}:
            raise ValueError("invalid receipt shape")
        UUID(str(payload["idempotency_key"]))
        if _fingerprint(path, root) != fingerprint:
            raise ValueError("receipt changed while it was being verified")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PublishedTileIntegrityError("published tile integrity validation failed") from error
    return payload, fingerprint


def _verify_file(
    path: Path,
    root: Path,
    *,
    expected_size: int | None,
    expected_sha256: str | None,
) -> _FileFingerprint:
    if expected_size is None or expected_sha256 is None:
        raise PublishedTileIntegrityError("published tile integrity validation failed")
    fingerprint = _verified_regular_file(path, root)
    try:
        matches = fingerprint.size == expected_size and _sha256(path) == expected_sha256
        unchanged = _fingerprint(path, root) == fingerprint
    except OSError as error:
        raise PublishedTileIntegrityError("published tile integrity validation failed") from error
    if not matches or not unchanged:
        raise PublishedTileIntegrityError("published tile integrity validation failed")
    return fingerprint


def _verified_regular_file(path: Path, root: Path) -> _FileFingerprint:
    if not _safe_path(path, root) or path.is_symlink() or not path.is_file():
        raise PublishedTileIntegrityError("published tile integrity validation failed")
    try:
        return _fingerprint(path, root)
    except OSError as error:
        raise PublishedTileIntegrityError("published tile integrity validation failed") from error


def _safe_directory(path: Path, root: Path, *, required: bool) -> bool:
    if not path.exists():
        if required:
            raise PublishedTileIntegrityError("published tile integrity validation failed")
        return False
    if not _safe_path(path, root) or path.is_symlink() or not path.is_dir():
        raise PublishedTileIntegrityError("published tile integrity validation failed")
    return True


def _safe_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    if current.is_symlink():
        return False
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            return False
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        return False
    return True


def _fingerprint(path: Path, root: Path) -> _FileFingerprint:
    metadata = path.stat()
    return _FileFingerprint(
        path=path,
        root=root,
        inode=metadata.st_ino,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
    )


def _fingerprints_are_unchanged(fingerprints: tuple[_FileFingerprint, ...]) -> bool:
    try:
        return all(
            _safe_path(fingerprint.path, fingerprint.root)
            and not fingerprint.path.is_symlink()
            and _fingerprint(fingerprint.path, fingerprint.root) == fingerprint
            for fingerprint in fingerprints
        )
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
