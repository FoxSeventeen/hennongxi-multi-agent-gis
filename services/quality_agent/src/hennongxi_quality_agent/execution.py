"""Idempotent orchestration of independent quality evaluation and reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from uuid import UUID

from hennongxi_contracts import QualityEvaluateCommand, QualityEvaluateResult

from hennongxi_quality_agent.artifacts import QualityArtifactStore
from hennongxi_quality_agent.evaluation import QualityEvaluator


@dataclass(frozen=True, slots=True)
class QualityOutcome:
    result: QualityEvaluateResult
    reused: bool


class QualityExecutor:
    """Evaluate once per task attempt and publish one verified quality report."""

    def __init__(
        self,
        manifest_path: Path,
        *,
        analysis_artifact_root: Path,
        report_store: QualityArtifactStore,
    ) -> None:
        self._manifest_path = manifest_path
        self._analysis_artifact_root = analysis_artifact_root
        self._report_store = report_store
        self._evaluator: QualityEvaluator | None = None
        self._evaluator_lock = Lock()

    def run(self, command: QualityEvaluateCommand, idempotency_key: UUID) -> QualityOutcome:
        with self._report_store.session(
            command.task_id,
            command.attempt,
            idempotency_key,
        ) as session:
            if session.existing_result is not None:
                return QualityOutcome(result=session.existing_result, reused=True)
            metrics = self._get_evaluator().evaluate(command)
            return QualityOutcome(
                result=session.publish(command, metrics),
                reused=False,
            )

    def _get_evaluator(self) -> QualityEvaluator:
        if self._evaluator is None:
            with self._evaluator_lock:
                if self._evaluator is None:
                    self._evaluator = QualityEvaluator(
                        self._manifest_path,
                        self._analysis_artifact_root,
                    )
        return self._evaluator
