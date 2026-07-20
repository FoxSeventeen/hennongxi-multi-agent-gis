"""Path-free Analysis Agent execution over verified local inputs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Final, cast
from uuid import UUID

import rasterio  # type: ignore[import-untyped]
from affine import Affine  # type: ignore[import-untyped]
from hennongxi_contracts import (
    AnalysisRunCommand,
    AnalysisRunResult,
    ArtifactType,
    DataAssetRef,
    LogicalDatasetId,
)
from hennongxi_contracts import (
    AreaStatistics as ContractAreaStatistics,
)
from rasterio.crs import CRS  # type: ignore[import-untyped]
from rasterio.errors import RasterioError  # type: ignore[import-untyped]

from hennongxi_analysis_agent.artifacts import (
    ANALYSIS_ARTIFACT_TYPES,
    AnalysisArtifactStore,
)
from hennongxi_analysis_agent.change import (
    AreaCalculationError,
    ClassifiedRaster,
    classify_change,
    summarize_class_areas,
)
from hennongxi_analysis_agent.change import (
    AreaStatistics as RasterAreaStatistics,
)
from hennongxi_analysis_agent.ndvi import calculate_difference, calculate_ndvi
from hennongxi_analysis_agent.raster import ContinuousRaster, GridMismatchError
from hennongxi_analysis_agent.raster_io import (
    GeoJSONGeometry,
    RasterClipError,
    RasterMetadataError,
    clip_band_to_geometry,
)

_RASTER_IDS: Final[tuple[LogicalDatasetId, ...]] = tuple(
    dataset_id for dataset_id in LogicalDatasetId if dataset_id is not LogicalDatasetId.WATERSHED
)


class AnalysisInputError(ValueError):
    """Raised when approved logical inputs cannot be reverified or processed safely."""


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    result: AnalysisRunResult
    reused: bool


@dataclass(frozen=True, slots=True)
class _ResolvedInputs:
    boundary_path: Path
    boundary_crs: CRS
    raster_paths: dict[LogicalDatasetId, Path]


class AnalysisExecutor:
    """Generate one complete atomic artifact set for a task attempt."""

    def __init__(
        self,
        manifest_path: Path,
        *,
        data_root: Path,
        cache_dir: Path,
        artifact_store: AnalysisArtifactStore,
    ) -> None:
        self._manifest_path = manifest_path
        self._data_root = data_root
        self._cache_dir = cache_dir
        self._artifact_store = artifact_store

    def run(self, command: AnalysisRunCommand, idempotency_key: UUID) -> AnalysisOutcome:
        with self._artifact_store.session(
            command.task_id,
            command.attempt,
            idempotency_key,
        ) as session:
            if session.existing_result is not None:
                return AnalysisOutcome(result=session.existing_result, reused=True)

            started = perf_counter()
            inputs = self._resolve_inputs(command.inputs)
            geometries = _load_geometries(inputs.boundary_path)
            before = self._calculate_ndvi(
                red_path=inputs.raster_paths[LogicalDatasetId.BEFORE_RED],
                nir_path=inputs.raster_paths[LogicalDatasetId.BEFORE_NIR],
                geometries=geometries,
                geometry_crs=inputs.boundary_crs,
            )
            after = self._calculate_ndvi(
                red_path=inputs.raster_paths[LogicalDatasetId.AFTER_RED],
                nir_path=inputs.raster_paths[LogicalDatasetId.AFTER_NIR],
                geometries=geometries,
                geometry_crs=inputs.boundary_crs,
            )
            try:
                difference = calculate_difference(after=after, before=before)
                classified = classify_change(difference)
                statistics = summarize_class_areas(classified)
            except (AreaCalculationError, GridMismatchError, ValueError) as error:
                raise AnalysisInputError(
                    "approved raster inputs cannot produce aligned metric analysis"
                ) from error
            if statistics.valid_pixel_count == 0:
                raise AnalysisInputError("approved raster inputs contain no valid analysis pixels")

            _write_continuous(session.path_for(ArtifactType.NDVI_BEFORE), before)
            _write_continuous(session.path_for(ArtifactType.NDVI_AFTER), after)
            _write_continuous(session.path_for(ArtifactType.NDVI_DIFFERENCE), difference)
            _write_classified(
                session.path_for(ArtifactType.CHANGE_CLASSIFICATION),
                classified,
            )
            _write_statistics(session.path_for(ArtifactType.AREA_STATISTICS), statistics)

            created_at = datetime.now(UTC)
            elapsed_ms = max(0, round((perf_counter() - started) * 1_000))
            result = AnalysisRunResult(
                task_id=command.task_id,
                step_id=command.step_id,
                attempt=command.attempt,
                correlation_id=command.correlation_id,
                artifacts=tuple(
                    self._artifact_store.artifact_ref(
                        session.staging_directory,
                        task_id=command.task_id,
                        attempt=command.attempt,
                        artifact_type=artifact_type,
                        created_at=created_at,
                    )
                    for artifact_type in ANALYSIS_ARTIFACT_TYPES
                ),
                statistics=_contract_statistics(statistics),
                elapsed_ms=elapsed_ms,
            )
            session.publish(result)
            return AnalysisOutcome(result=result, reused=False)

    def _resolve_inputs(self, inputs: tuple[DataAssetRef, ...]) -> _ResolvedInputs:
        try:
            manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            entries = manifest["assets"]
            if not isinstance(entries, list):
                raise TypeError("manifest assets must be a list")
            by_id = {
                LogicalDatasetId(entry["logical_id"]): entry
                for entry in entries
                if isinstance(entry, dict)
            }
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise AnalysisInputError("approved data manifest is unavailable or invalid") from error

        command_assets = {asset.dataset_id: asset for asset in inputs}
        if set(by_id) != set(LogicalDatasetId):
            raise AnalysisInputError("approved data manifest does not contain the required assets")

        verified: dict[LogicalDatasetId, Path] = {}
        for dataset_id in LogicalDatasetId:
            entry = by_id[dataset_id]
            asset = command_assets[dataset_id]
            path = self._resolve_entry_path(dataset_id, entry)
            self._verify_asset_metadata(asset, entry, path)
            if dataset_id is not LogicalDatasetId.WATERSHED:
                self._verify_raster_grid(asset, path)
            verified[dataset_id] = path

        boundary_entry = by_id[LogicalDatasetId.WATERSHED]
        try:
            boundary_crs = CRS.from_user_input(boundary_entry["crs"])
        except (KeyError, TypeError, ValueError, RasterioError) as error:
            raise AnalysisInputError("approved watershed CRS is invalid") from error
        return _ResolvedInputs(
            boundary_path=verified[LogicalDatasetId.WATERSHED],
            boundary_crs=boundary_crs,
            raster_paths={dataset_id: verified[dataset_id] for dataset_id in _RASTER_IDS},
        )

    def _resolve_entry_path(
        self,
        dataset_id: LogicalDatasetId,
        entry: dict[str, object],
    ) -> Path:
        expected_storage = "bundle" if dataset_id is LogicalDatasetId.WATERSHED else "cache"
        if entry.get("storage") != expected_storage:
            raise AnalysisInputError("approved asset storage class is invalid")
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute():
            raise AnalysisInputError("approved asset path is outside its approved storage root")

        root = self._data_root if expected_storage == "bundle" else self._cache_dir
        resolved_root = root.resolve()
        path = (resolved_root / raw_path).resolve(strict=False)
        try:
            path.relative_to(resolved_root)
        except ValueError as error:
            raise AnalysisInputError(
                "approved asset path is outside its approved storage root"
            ) from error
        if not path.is_file():
            raise AnalysisInputError("approved local asset is unavailable")
        return path

    @staticmethod
    def _verify_asset_metadata(
        asset: DataAssetRef,
        entry: dict[str, object],
        path: Path,
    ) -> None:
        if (
            entry.get("sha256") != asset.checksum_sha256
            or entry.get("byte_size") != asset.byte_size
            or path.stat().st_size != asset.byte_size
            or _sha256(path) != asset.checksum_sha256
        ):
            raise AnalysisInputError("approved input metadata does not match local content")

    @staticmethod
    def _verify_raster_grid(asset: DataAssetRef, path: Path) -> None:
        if asset.grid is None:
            raise AnalysisInputError("approved raster input is missing grid metadata")
        try:
            with rasterio.open(path) as dataset:
                actual_bounds = tuple(float(value) for value in dataset.bounds)
                expected_transform = Affine(*asset.grid.transform)
                if (
                    dataset.crs is None
                    or dataset.crs != CRS.from_user_input(asset.grid.crs)
                    or dataset.width != asset.grid.width
                    or dataset.height != asset.grid.height
                    or dataset.transform != expected_transform
                    or actual_bounds != asset.grid.bounds
                    or dataset.nodata is None
                    or not math.isclose(dataset.nodata, asset.grid.nodata)
                ):
                    raise AnalysisInputError(
                        "approved raster grid metadata does not match local content"
                    )
        except AnalysisInputError:
            raise
        except (OSError, ValueError, RasterioError) as error:
            raise AnalysisInputError("approved raster input is unreadable") from error

    @staticmethod
    def _calculate_ndvi(
        *,
        red_path: Path,
        nir_path: Path,
        geometries: tuple[GeoJSONGeometry, ...],
        geometry_crs: CRS,
    ) -> ContinuousRaster:
        try:
            with rasterio.open(red_path) as red_dataset, rasterio.open(nir_path) as nir_dataset:
                red = clip_band_to_geometry(
                    red_dataset,
                    geometries=geometries,
                    geometry_crs=geometry_crs,
                )
                nir = clip_band_to_geometry(
                    nir_dataset,
                    geometries=geometries,
                    geometry_crs=geometry_crs,
                )
                return calculate_ndvi(nir=nir, red=red)
        except (OSError, ValueError, RasterioError, RasterClipError, RasterMetadataError) as error:
            raise AnalysisInputError(
                "approved raster input cannot be clipped and analyzed"
            ) from error


def _load_geometries(path: Path) -> tuple[GeoJSONGeometry, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        features = payload["features"]
        if not isinstance(features, list):
            raise TypeError("features must be a list")
        geometries = tuple(
            cast(GeoJSONGeometry, feature["geometry"])
            for feature in features
            if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
        )
        if not geometries:
            raise ValueError("boundary has no geometries")
        return geometries
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise AnalysisInputError("approved watershed boundary is invalid") from error


def _write_continuous(path: Path, raster: ContinuousRaster) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=raster.grid.width,
        height=raster.grid.height,
        count=1,
        dtype="float32",
        crs=raster.grid.crs,
        transform=raster.grid.transform,
        nodata=raster.nodata,
        compress="deflate",
        predictor=3,
    ) as dataset:
        dataset.write(raster.values, 1)


def _write_classified(path: Path, raster: ClassifiedRaster) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=raster.grid.width,
        height=raster.grid.height,
        count=1,
        dtype="int8",
        crs=raster.grid.crs,
        transform=raster.grid.transform,
        nodata=raster.nodata,
        compress="deflate",
    ) as dataset:
        dataset.write(raster.values, 1)
        dataset.update_tags(change_threshold=str(raster.threshold))


def _write_statistics(path: Path, statistics: RasterAreaStatistics) -> None:
    path.write_text(
        json.dumps(asdict(statistics), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _contract_statistics(statistics: RasterAreaStatistics) -> ContractAreaStatistics:
    return ContractAreaStatistics(
        increase_hectares=statistics.increase_area_square_metres / 10_000,
        stable_hectares=statistics.stable_area_square_metres / 10_000,
        decrease_hectares=statistics.decrease_area_square_metres / 10_000,
        valid_hectares=statistics.valid_area_square_metres / 10_000,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
