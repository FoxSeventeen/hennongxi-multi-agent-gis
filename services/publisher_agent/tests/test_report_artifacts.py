from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from hennongxi_contracts import ArtifactStatus, ArtifactType
from hennongxi_publisher_agent.report_artifacts import (
    ReportArtifactConflictError,
    ReportArtifactIntegrityError,
    ReportArtifactStore,
)

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
OTHER_KEY = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
CREATED_AT = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
PDF_PAYLOAD = b"%PDF-1.4\n% task-bound report fixture\n%%EOF\n"


def test_report_store_atomically_publishes_fixed_task_bound_artifact(tmp_path: Path) -> None:
    outcome = ReportArtifactStore(tmp_path).publish(
        task_id=TASK_ID,
        attempt=1,
        idempotency_key=IDEMPOTENCY_KEY,
        created_at=CREATED_AT,
        payload=PDF_PAYLOAD,
    )

    expected_directory = tmp_path / str(TASK_ID) / "attempt-1" / "publisher"
    assert outcome.path == expected_directory / "report.pdf"
    assert outcome.path.read_bytes() == PDF_PAYLOAD
    assert outcome.reused is False
    assert outcome.artifact.artifact_id == UUID("b0944bbf-5184-527b-91f9-4c0fdc5609c5")
    assert outcome.artifact.task_id == TASK_ID
    assert outcome.artifact.attempt == 1
    assert outcome.artifact.artifact_type is ArtifactType.PDF_REPORT
    assert outcome.artifact.status is ArtifactStatus.COMPLETE
    assert outcome.artifact.media_type == "application/pdf"
    assert outcome.artifact.created_at == CREATED_AT
    assert outcome.artifact.byte_size == len(PDF_PAYLOAD)
    assert outcome.artifact.checksum_sha256 == (
        "2460cae00ddcd723f9731db359d5c6356b733b28546025e878413972e61acad2"
    )
    receipt = json.loads((expected_directory / "report_result.json").read_text())
    assert receipt == {
        "idempotency_key": str(IDEMPOTENCY_KEY),
        "artifact": outcome.artifact.model_dump(mode="json"),
    }
    assert not tuple(expected_directory.parent.glob(".publisher-staging-*"))


def test_report_store_reverifies_and_reuses_same_idempotency_key(tmp_path: Path) -> None:
    store = ReportArtifactStore(tmp_path)
    first = store.publish(
        task_id=TASK_ID,
        attempt=1,
        idempotency_key=IDEMPOTENCY_KEY,
        created_at=CREATED_AT,
        payload=PDF_PAYLOAD,
    )
    modified_ns = first.path.stat().st_mtime_ns

    second = store.publish(
        task_id=TASK_ID,
        attempt=1,
        idempotency_key=IDEMPOTENCY_KEY,
        created_at=datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
        payload=b"%PDF-1.4\nnew bytes that must not overwrite\n%%EOF\n",
    )

    assert second.reused is True
    assert second.artifact == first.artifact
    assert second.path.read_bytes() == PDF_PAYLOAD
    assert second.path.stat().st_mtime_ns == modified_ns


def test_report_store_rejects_conflict_and_visible_corruption(tmp_path: Path) -> None:
    store = ReportArtifactStore(tmp_path)
    first = store.publish(
        task_id=TASK_ID,
        attempt=1,
        idempotency_key=IDEMPOTENCY_KEY,
        created_at=CREATED_AT,
        payload=PDF_PAYLOAD,
    )

    with pytest.raises(ReportArtifactConflictError, match="different idempotency key"):
        store.publish(
            task_id=TASK_ID,
            attempt=1,
            idempotency_key=OTHER_KEY,
            created_at=CREATED_AT,
            payload=PDF_PAYLOAD,
        )

    first.path.write_bytes(b"corrupted")
    with pytest.raises(ReportArtifactIntegrityError, match="published PDF report is invalid"):
        store.publish(
            task_id=TASK_ID,
            attempt=1,
            idempotency_key=IDEMPOTENCY_KEY,
            created_at=CREATED_AT,
            payload=PDF_PAYLOAD,
        )
    assert first.path.read_bytes() == b"corrupted"


def test_report_store_cleans_staging_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated storage failure")

    monkeypatch.setattr("hennongxi_publisher_agent.report_artifacts.os.replace", _fail_replace)
    store = ReportArtifactStore(tmp_path)

    with pytest.raises(OSError, match="simulated storage failure"):
        store.publish(
            task_id=TASK_ID,
            attempt=1,
            idempotency_key=IDEMPOTENCY_KEY,
            created_at=CREATED_AT,
            payload=PDF_PAYLOAD,
        )

    attempt_directory = tmp_path / str(TASK_ID) / "attempt-1"
    assert not (attempt_directory / "publisher").exists()
    assert not tuple(attempt_directory.glob(".publisher-staging-*"))


def test_report_store_rejects_symbolic_task_storage_component(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (artifact_root / str(TASK_ID)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ReportArtifactIntegrityError, match="real directory"):
        ReportArtifactStore(artifact_root).publish(
            task_id=TASK_ID,
            attempt=1,
            idempotency_key=IDEMPOTENCY_KEY,
            created_at=CREATED_AT,
            payload=PDF_PAYLOAD,
        )

    assert not (outside / "attempt-1").exists()


@pytest.mark.parametrize("payload", [b"", b"not a PDF", b"%PDF-1.4\nmissing trailer"])
def test_report_store_rejects_invalid_pdf_bytes_before_writing(
    tmp_path: Path,
    payload: bytes,
) -> None:
    with pytest.raises(ReportArtifactIntegrityError, match="valid PDF bytes"):
        ReportArtifactStore(tmp_path).publish(
            task_id=TASK_ID,
            attempt=1,
            idempotency_key=IDEMPOTENCY_KEY,
            created_at=CREATED_AT,
            payload=payload,
        )

    assert not (tmp_path / str(TASK_ID)).exists()
