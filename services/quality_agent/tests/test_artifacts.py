from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from hennongxi_contracts import (
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

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


def _command() -> QualityEvaluateCommand:
    return QualityEvaluateCommand(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=(),
        analysis_elapsed_ms=1250,
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
