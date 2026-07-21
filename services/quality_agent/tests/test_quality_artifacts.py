from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from hennongxi_contracts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    QualityConclusion,
    QualityEvaluateCommand,
    QualityMetrics,
    QualityThresholds,
)
from hennongxi_quality_agent.artifacts import (
    QualityArtifactConflictError,
    QualityArtifactIntegrityError,
    QualityArtifactStore,
)
from hennongxi_quality_agent.execution import QualityExecutor

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
SHA256 = "a" * 64


def _command(
    *,
    attempt: int = 1,
    artifacts: tuple[ArtifactRef, ...] = (),
    reuse_from_attempt: int | None = None,
) -> QualityEvaluateCommand:
    return QualityEvaluateCommand(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=attempt,
        correlation_id=CORRELATION_ID,
        artifacts=artifacts,
        analysis_elapsed_ms=1250,
        reuse_from_attempt=reuse_from_attempt,
    )


def _metrics() -> QualityMetrics:
    return QualityMetrics(
        coverage_ratio=0.95,
        valid_pixel_ratio=0.90,
        output_complete=True,
        elapsed_ms=1250,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.90,
        ),
        conclusion=QualityConclusion.PASS,
        passed=True,
        evidence=("覆盖达标", "有效像元达标", "输出完整", "耗时已记录"),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _analysis_artifacts(attempt: int) -> tuple[ArtifactRef, ...]:
    return tuple(
        ArtifactRef(
            artifact_id=uuid5(TASK_ID, f"{attempt}:{artifact_type.value}"),
            task_id=TASK_ID,
            attempt=attempt,
            artifact_type=artifact_type,
            status=ArtifactStatus.COMPLETE,
            media_type=(
                "application/json"
                if artifact_type is ArtifactType.AREA_STATISTICS
                else "image/tiff; application=geotiff"
            ),
            created_at=datetime(2026, 7, 21, tzinfo=UTC),
            checksum_sha256=SHA256,
            byte_size=1,
        )
        for artifact_type in (
            ArtifactType.NDVI_BEFORE,
            ArtifactType.NDVI_AFTER,
            ArtifactType.NDVI_DIFFERENCE,
            ArtifactType.CHANGE_CLASSIFICATION,
            ArtifactType.AREA_STATISTICS,
        )
    )


class _StubEvaluator:
    def evaluate(self, _command: QualityEvaluateCommand) -> QualityMetrics:
        return _metrics()


def test_store_atomically_publishes_and_reuses_a_verified_report(tmp_path: Path) -> None:
    store = QualityArtifactStore(tmp_path / "quality-reports")
    command = _command()

    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
        assert session.existing_result is None
        result = session.publish(command, _metrics())

    final_directory = store.final_directory(TASK_ID, 1)
    report_path = final_directory / "quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["metrics"]["conclusion"] == "PASS"
    assert result.artifact.checksum_sha256 == _sha256(report_path)
    assert result.artifact.byte_size == report_path.stat().st_size
    assert not any(final_directory.parent.glob(".quality-staging-*"))

    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as repeated:
        assert repeated.existing_result == result


def test_executor_promotes_only_a_verified_prior_quality_result(tmp_path: Path) -> None:
    store = QualityArtifactStore(tmp_path / "quality-reports")
    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
        source = session.publish(_command(), _metrics())
    executor = QualityExecutor(
        tmp_path / "missing-manifest.json",
        analysis_artifact_root=tmp_path / "missing-analysis",
        report_store=store,
    )
    executor._evaluator = _StubEvaluator()  # type: ignore[assignment]
    command = _command(
        attempt=2,
        artifacts=_analysis_artifacts(2),
        reuse_from_attempt=1,
    )

    promoted = executor.run(
        command,
        UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
    )

    assert promoted.reused
    assert promoted.result.attempt == 2
    assert promoted.result.metrics == source.metrics
    assert promoted.result.artifact.attempt == 2

    source_report = store.final_directory(TASK_ID, 1) / "quality_report.json"
    with source_report.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(QualityArtifactIntegrityError, match="quality report"):
        executor.run(
            _command(
                attempt=3,
                artifacts=_analysis_artifacts(3),
                reuse_from_attempt=1,
            ),
            UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
        )


def test_store_rejects_a_different_idempotency_key_for_the_same_attempt(
    tmp_path: Path,
) -> None:
    store = QualityArtifactStore(tmp_path / "quality-reports")
    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
        session.publish(_command(), _metrics())

    with pytest.raises(QualityArtifactConflictError, match="different idempotency key"):
        with store.session(
            TASK_ID,
            1,
            UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        ):
            pass


def test_store_rejects_a_tampered_published_report(tmp_path: Path) -> None:
    store = QualityArtifactStore(tmp_path / "quality-reports")
    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
        session.publish(_command(), _metrics())

    report_path = store.final_directory(TASK_ID, 1) / "quality_report.json"
    with report_path.open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(QualityArtifactIntegrityError, match="quality report"):
        with store.session(TASK_ID, 1, IDEMPOTENCY_KEY):
            pass


def test_store_rejects_a_symlinked_idempotency_receipt(tmp_path: Path) -> None:
    store = QualityArtifactStore(tmp_path / "quality-reports")
    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
        session.publish(_command(), _metrics())

    receipt_path = store.final_directory(TASK_ID, 1) / "quality_result.json"
    outside_receipt = tmp_path / "outside-receipt.json"
    outside_receipt.write_bytes(receipt_path.read_bytes())
    receipt_path.unlink()
    receipt_path.symlink_to(outside_receipt)

    with pytest.raises(QualityArtifactIntegrityError, match="quality report"):
        with store.session(TASK_ID, 1, IDEMPOTENCY_KEY):
            pass


def test_store_never_publishes_an_abandoned_staging_directory(tmp_path: Path) -> None:
    store = QualityArtifactStore(tmp_path / "quality-reports")

    with pytest.raises(RuntimeError, match="forced failure"):
        with store.session(TASK_ID, 1, IDEMPOTENCY_KEY):
            raise RuntimeError("forced failure")

    assert not store.final_directory(TASK_ID, 1).exists()
    assert not any(store.final_directory(TASK_ID, 1).parent.glob(".quality-staging-*"))
