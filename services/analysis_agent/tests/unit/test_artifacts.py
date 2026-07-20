from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from hennongxi_analysis_agent.artifacts import (
    ANALYSIS_ARTIFACT_TYPES,
    AnalysisArtifactStore,
    ArtifactConflictError,
    ArtifactIntegrityError,
)
from hennongxi_contracts import AnalysisRunResult, AreaStatistics, ArtifactType

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CREATED_AT = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _write_complete_set(directory: Path) -> None:
    for artifact_type in ANALYSIS_ARTIFACT_TYPES:
        suffix = ".json" if artifact_type is ArtifactType.AREA_STATISTICS else ".tif"
        (directory / f"{artifact_type.value.lower()}{suffix}").write_bytes(
            artifact_type.value.encode()
        )


def _result(store: AnalysisArtifactStore, staging_directory: Path) -> AnalysisRunResult:
    return AnalysisRunResult(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=tuple(
            store.artifact_ref(
                staging_directory,
                task_id=TASK_ID,
                attempt=1,
                artifact_type=artifact_type,
                created_at=CREATED_AT,
            )
            for artifact_type in ANALYSIS_ARTIFACT_TYPES
        ),
        statistics=AreaStatistics(
            increase_hectares=1,
            stable_hectares=2,
            decrease_hectares=3,
            valid_hectares=6,
        ),
        elapsed_ms=25,
    )


def test_complete_artifact_set_is_published_and_reused_by_idempotency_key(
    tmp_path: Path,
) -> None:
    store = AnalysisArtifactStore(tmp_path)

    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
        assert session.existing_result is None
        _write_complete_set(session.staging_directory)
        expected = _result(store, session.staging_directory)
        session.publish(expected)

    final_directory = store.final_directory(TASK_ID, 1)
    assert final_directory.is_dir()
    assert not tuple(final_directory.parent.glob(".analysis-staging-*"))

    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as repeated:
        assert repeated.existing_result == expected


def test_failed_session_removes_staging_without_publishing_partial_artifacts(
    tmp_path: Path,
) -> None:
    store = AnalysisArtifactStore(tmp_path)

    with pytest.raises(RuntimeError, match="injected failure"):
        with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
            session.path_for(ArtifactType.NDVI_BEFORE).write_bytes(b"partial")
            raise RuntimeError("injected failure")

    attempt_directory = store.final_directory(TASK_ID, 1).parent
    assert not store.final_directory(TASK_ID, 1).exists()
    assert not tuple(attempt_directory.glob(".analysis-staging-*"))


def test_corrupt_published_artifact_is_never_reused(tmp_path: Path) -> None:
    store = AnalysisArtifactStore(tmp_path)
    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
        _write_complete_set(session.staging_directory)
        session.publish(_result(store, session.staging_directory))

    artifact_path = store.final_directory(TASK_ID, 1) / "ndvi_before.tif"
    artifact_path.write_bytes(b"x" * artifact_path.stat().st_size)

    with pytest.raises(ArtifactIntegrityError, match="checksum"):
        with store.session(TASK_ID, 1, IDEMPOTENCY_KEY):
            pass


def test_different_idempotency_key_cannot_replace_a_published_attempt(tmp_path: Path) -> None:
    store = AnalysisArtifactStore(tmp_path)
    with store.session(TASK_ID, 1, IDEMPOTENCY_KEY) as session:
        _write_complete_set(session.staging_directory)
        session.publish(_result(store, session.staging_directory))

    with pytest.raises(ArtifactConflictError, match="different idempotency key"):
        with store.session(
            TASK_ID,
            1,
            UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        ):
            pass
