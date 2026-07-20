"""Independent raster inspection for Analysis Agent outputs."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Final

import numpy as np
import rasterio  # type: ignore[import-untyped]
from hennongxi_contracts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    QualityConclusion,
    QualityEvaluateCommand,
    QualityMetrics,
)
from rasterio.errors import RasterioError  # type: ignore[import-untyped]
from rasterio.features import geometry_mask  # type: ignore[import-untyped]

from hennongxi_quality_agent.configuration import (
    ReferenceGrid,
    load_quality_configuration,
)

RASTER_ARTIFACT_TYPES: Final[tuple[ArtifactType, ...]] = (
    ArtifactType.NDVI_BEFORE,
    ArtifactType.NDVI_AFTER,
    ArtifactType.NDVI_DIFFERENCE,
    ArtifactType.CHANGE_CLASSIFICATION,
)
REQUIRED_ARTIFACT_TYPES: Final[tuple[ArtifactType, ...]] = (
    *RASTER_ARTIFACT_TYPES,
    ArtifactType.AREA_STATISTICS,
)
_FILENAMES: Final[dict[ArtifactType, str]] = {
    **{
        artifact_type: f"{artifact_type.value.lower()}.tif"
        for artifact_type in RASTER_ARTIFACT_TYPES
    },
    ArtifactType.AREA_STATISTICS: "area_statistics.json",
}
_MEDIA_TYPES: Final[dict[ArtifactType, str]] = {
    **{artifact_type: "image/tiff; application=geotiff" for artifact_type in RASTER_ARTIFACT_TYPES},
    ArtifactType.AREA_STATISTICS: "application/json",
}
_STATISTIC_FIELDS = frozenset(
    {
        "threshold",
        "pixel_area_square_metres",
        "valid_pixel_count",
        "decrease_pixel_count",
        "stable_pixel_count",
        "increase_pixel_count",
        "valid_area_square_metres",
        "decrease_area_square_metres",
        "stable_area_square_metres",
        "increase_area_square_metres",
    }
)


class QualityEvaluator:
    """Resolve only fixed task artifacts and compute transparent quality gates."""

    def __init__(self, manifest_path: Path, artifact_root: Path) -> None:
        self._configuration = load_quality_configuration(manifest_path)
        self._artifact_root = artifact_root

    def evaluate(self, command: QualityEvaluateCommand) -> QualityMetrics:
        directory = (
            self._artifact_root / str(command.task_id) / f"attempt-{command.attempt}" / "analysis"
        )
        refs = {artifact.artifact_type: artifact for artifact in command.artifacts}
        coverage_ratios: list[float] = []
        valid_ratios: list[float] = []
        verified_count = 0
        classification_valid_count: int | None = None

        for artifact_type in RASTER_ARTIFACT_TYPES:
            artifact = refs.get(artifact_type)
            path = directory / _FILENAMES[artifact_type]
            if artifact is None or not _matches_ref(path, artifact, artifact_type):
                coverage_ratios.append(0.0)
                valid_ratios.append(0.0)
                continue
            inspection = _inspect_raster(path, artifact_type, self._configuration.grid)
            if inspection is None:
                coverage_ratios.append(0.0)
                valid_ratios.append(0.0)
                continue
            coverage, valid, valid_count = inspection
            coverage_ratios.append(coverage)
            valid_ratios.append(valid)
            verified_count += 1
            if artifact_type is ArtifactType.CHANGE_CLASSIFICATION:
                classification_valid_count = valid_count

        statistics_ref = refs.get(ArtifactType.AREA_STATISTICS)
        statistics_path = directory / _FILENAMES[ArtifactType.AREA_STATISTICS]
        if (
            statistics_ref is not None
            and _matches_ref(
                statistics_path,
                statistics_ref,
                ArtifactType.AREA_STATISTICS,
            )
            and _statistics_are_valid(statistics_path, classification_valid_count)
        ):
            verified_count += 1

        coverage_ratio = min(coverage_ratios, default=0.0)
        valid_pixel_ratio = min(valid_ratios, default=0.0)
        output_complete = verified_count == len(REQUIRED_ARTIFACT_TYPES)
        thresholds = self._configuration.thresholds
        passed = (
            coverage_ratio >= thresholds.minimum_watershed_coverage_ratio
            and valid_pixel_ratio >= thresholds.minimum_valid_pixel_ratio
            and output_complete
        )
        conclusion = QualityConclusion.PASS if passed else QualityConclusion.FAIL
        return QualityMetrics(
            coverage_ratio=coverage_ratio,
            valid_pixel_ratio=valid_pixel_ratio,
            output_complete=output_complete,
            elapsed_ms=command.analysis_elapsed_ms,
            thresholds=thresholds,
            conclusion=conclusion,
            passed=passed,
            evidence=(
                f"流域覆盖率 {coverage_ratio:.4f}，阈值 >= "
                f"{thresholds.minimum_watershed_coverage_ratio:.4f}",
                f"有效像元率 {valid_pixel_ratio:.4f}，阈值 >= "
                f"{thresholds.minimum_valid_pixel_ratio:.4f}",
                f"输出完整性 {verified_count}/{len(REQUIRED_ARTIFACT_TYPES)}，要求 "
                f"{len(REQUIRED_ARTIFACT_TYPES)}/{len(REQUIRED_ARTIFACT_TYPES)}",
                f"Analysis 耗时 {command.analysis_elapsed_ms} ms，要求为非负整数",
            ),
        )


def _matches_ref(path: Path, artifact: ArtifactRef, artifact_type: ArtifactType) -> bool:
    try:
        matches_metadata = (
            artifact.status is ArtifactStatus.COMPLETE
            and artifact.media_type == _MEDIA_TYPES[artifact_type]
            and not path.is_symlink()
            and path.is_file()
            and artifact.byte_size is not None
            and artifact.checksum_sha256 is not None
            and path.stat().st_size == artifact.byte_size
        )
        return bool(matches_metadata and _sha256(path) == artifact.checksum_sha256)
    except OSError:
        return False


def _inspect_raster(
    path: Path,
    artifact_type: ArtifactType,
    reference: ReferenceGrid,
) -> tuple[float, float, int] | None:
    try:
        with rasterio.open(path) as dataset:
            if not _grid_is_safe(dataset, reference) or dataset.nodata is None:
                return None
            values = dataset.read([1])[0]
            inside = geometry_mask(
                reference.watershed_geometries,
                out_shape=values.shape,
                transform=dataset.transform,
                invert=True,
            )
            covered_count = int(np.count_nonzero(inside))
            if covered_count == 0:
                return None
            valid = inside & (dataset.read_masks([1])[0] > 0) & np.isfinite(values)
            if not _values_are_valid(values, valid, artifact_type):
                return None
            valid_count = int(np.count_nonzero(valid))

            left, bottom, right, top = dataset.bounds
            extent = {
                "type": "Polygon",
                "coordinates": [
                    [[left, bottom], [right, bottom], [right, top], [left, top], [left, bottom]]
                ],
            }
            extent_mask = geometry_mask(
                [extent],
                out_shape=(reference.height, reference.width),
                transform=reference.transform,
                invert=True,
            )
            watershed_count = int(np.count_nonzero(reference.watershed_mask))
            coverage_count = int(np.count_nonzero(reference.watershed_mask & extent_mask))
            return (
                coverage_count / watershed_count,
                valid_count / covered_count,
                valid_count,
            )
    except (OSError, OverflowError, ValueError, RasterioError):
        return None


def _grid_is_safe(dataset: rasterio.io.DatasetReader, reference: ReferenceGrid) -> bool:
    transform = dataset.transform
    x_offset = (transform.c - reference.transform.c) / reference.transform.a
    y_offset = (transform.f - reference.transform.f) / reference.transform.e
    return bool(
        dataset.count == 1
        and dataset.crs == reference.crs
        and dataset.width <= reference.width
        and dataset.height <= reference.height
        and math.isclose(transform.a, reference.transform.a, abs_tol=1e-9)
        and math.isclose(transform.e, reference.transform.e, abs_tol=1e-9)
        and math.isclose(transform.b, 0.0, abs_tol=1e-12)
        and math.isclose(transform.d, 0.0, abs_tol=1e-12)
        and math.isclose(x_offset, round(x_offset), abs_tol=1e-9)
        and math.isclose(y_offset, round(y_offset), abs_tol=1e-9)
    )


def _values_are_valid(
    values: np.ndarray,
    valid: np.ndarray,
    artifact_type: ArtifactType,
) -> bool:
    valid_values = values[valid]
    if artifact_type is ArtifactType.CHANGE_CLASSIFICATION:
        return bool(np.all(np.isin(valid_values, (-1, 0, 1))))
    limit = 2.0 if artifact_type is ArtifactType.NDVI_DIFFERENCE else 1.0
    return bool(np.all((valid_values >= -limit) & (valid_values <= limit)))


def _statistics_are_valid(path: Path, classification_valid_count: int | None) -> bool:
    if classification_valid_count is None:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != _STATISTIC_FIELDS:
            return False
        counts = [
            _nonnegative_integer(payload[field])
            for field in (
                "decrease_pixel_count",
                "stable_pixel_count",
                "increase_pixel_count",
            )
        ]
        valid_count = _nonnegative_integer(payload["valid_pixel_count"])
        pixel_area = _finite_number(payload["pixel_area_square_metres"])
        threshold = _finite_number(payload["threshold"])
        if (
            any(count < 0 for count in counts)
            or valid_count != sum(counts)
            or valid_count != classification_valid_count
            or not math.isfinite(pixel_area)
            or pixel_area <= 0
            or not math.isfinite(threshold)
            or threshold <= 0
        ):
            return False
        for label, count in zip(("decrease", "stable", "increase"), counts, strict=True):
            if not math.isclose(
                _finite_number(payload[f"{label}_area_square_metres"]),
                count * pixel_area,
                abs_tol=1e-6,
            ):
                return False
        return math.isclose(
            _finite_number(payload["valid_area_square_metres"]),
            valid_count * pixel_area,
            abs_tol=1e-6,
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _nonnegative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("pixel counts must be nonnegative integers")
    return value


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a finite JSON number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("expected a finite JSON number")
    return number


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
