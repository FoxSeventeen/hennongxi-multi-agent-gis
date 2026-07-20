"""Build task-bound browser metadata from verified artifacts and approved source data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

import rasterio  # type: ignore[import-untyped]
from hennongxi_contracts import (
    PublishedResource,
    PublisherPublishCommand,
    PublisherPublishResult,
    TileArtifactType,
    TileMetadata,
)
from rasterio.errors import RasterioError  # type: ignore[import-untyped]
from rasterio.warp import transform_bounds  # type: ignore[import-untyped]

from hennongxi_publisher_agent.catalog import ResolvedPublication
from hennongxi_publisher_agent.report import ReportContent, ReportInputError, render_report
from hennongxi_publisher_agent.report_artifacts import ReportArtifactStore
from hennongxi_publisher_agent.tiles import style_for


class PublicationConfigurationError(RuntimeError):
    """Raised when approved source dates or attribution cannot be loaded safely."""


class PublicationArtifactError(RuntimeError):
    """Raised when a verified raster cannot provide safe public metadata."""


class PublicationCatalog(Protocol):
    def resolve_publication(self, command: PublisherPublishCommand) -> ResolvedPublication: ...


@dataclass(frozen=True, slots=True)
class _SourceMetadata:
    before_date: date
    after_date: date
    attribution: str


class PublicationService:
    """Create deterministic public resources without exposing local storage locations."""

    def __init__(
        self,
        catalog: PublicationCatalog,
        manifest_path: Path,
        report_store: ReportArtifactStore,
    ) -> None:
        self._catalog = catalog
        self._manifest_path = manifest_path
        self._report_store = report_store

    def publish(
        self,
        command: PublisherPublishCommand,
        idempotency_key: UUID,
    ) -> PublisherPublishResult:
        publication = self._catalog.resolve_publication(command)
        if (
            publication.attempt != command.attempt
            or publication.correlation_id != command.correlation_id
            or publication.analysis.task_id != command.task_id
            or publication.analysis.attempt != command.attempt
            or publication.analysis.correlation_id != command.correlation_id
            or publication.quality.task_id != command.task_id
            or publication.quality.attempt != command.attempt
            or publication.quality.correlation_id != command.correlation_id
            or publication.quality.metrics != command.quality
            or {
                artifact.artifact_type: artifact
                for artifact in (
                    *publication.analysis.artifacts,
                    publication.quality.artifact,
                )
            }
            != {artifact.artifact_type: artifact for artifact in command.artifacts}
        ):
            raise PublicationArtifactError("publication scope does not match the command")
        source_metadata = _load_source_metadata(self._manifest_path)
        tile_map = {
            TileArtifactType(tile.artifact.artifact_type.value): tile for tile in publication.tiles
        }
        if len(tile_map) != 4 or set(tile_map) != set(TileArtifactType):
            raise PublicationArtifactError("publication requires four verified tile artifacts")

        resources = []
        for artifact_type in TileArtifactType:
            tile = tile_map[artifact_type]
            if tile.artifact.task_id != command.task_id or tile.artifact.attempt != command.attempt:
                raise PublicationArtifactError(
                    "publication artifact scope does not match the command"
                )
            start_date, end_date = _dates_for(artifact_type, source_metadata)
            style = style_for(artifact_type)
            resources.append(
                PublishedResource(
                    artifact_id=tile.artifact.artifact_id,
                    tile_template=(
                        f"/api/v1/tiles/{command.task_id}/{artifact_type.value}/"
                        "{z}/{x}/{y}.png"
                    ),
                    tile_metadata=TileMetadata(
                        artifact_type=artifact_type,
                        bounds_wgs84=_wgs84_bounds(tile.path),
                        start_date=start_date,
                        end_date=end_date,
                        units=style.units,
                        attribution=source_metadata.attribution,
                        legend=style.legend,
                    ),
                )
            )

        try:
            report_created_at = datetime.now(UTC)
            report_outcome = self._report_store.publish(
                task_id=command.task_id,
                attempt=command.attempt,
                idempotency_key=idempotency_key,
                created_at=report_created_at,
                payload=render_report(
                    ReportContent(
                        task_id=command.task_id,
                        attempt=command.attempt,
                        correlation_id=command.correlation_id,
                        created_at=report_created_at,
                        before_date=source_metadata.before_date,
                        after_date=source_metadata.after_date,
                        attribution=source_metadata.attribution,
                        statistics=publication.analysis.statistics,
                        quality=publication.quality.metrics,
                        artifacts=(
                            *publication.analysis.artifacts,
                            publication.quality.artifact,
                        ),
                    )
                ),
            )
        except ReportInputError as error:
            raise PublicationArtifactError("publication report inputs are incomplete") from error
        resources.append(
            PublishedResource(
                artifact_id=report_outcome.artifact.artifact_id,
                download_path=(
                    f"/api/v1/tasks/{command.task_id}/artifacts/"
                    f"{report_outcome.artifact.artifact_id}/download"
                ),
            )
        )
        return PublisherPublishResult(
            task_id=command.task_id,
            step_id=command.step_id,
            attempt=command.attempt,
            correlation_id=command.correlation_id,
            resources=tuple(resources),
            report=report_outcome.artifact,
        )


def _dates_for(
    artifact_type: TileArtifactType,
    source: _SourceMetadata,
) -> tuple[date, date]:
    if artifact_type is TileArtifactType.NDVI_BEFORE:
        return source.before_date, source.before_date
    if artifact_type is TileArtifactType.NDVI_AFTER:
        return source.after_date, source.after_date
    return source.before_date, source.after_date


def _wgs84_bounds(path: Path) -> tuple[float, float, float, float]:
    try:
        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                raise ValueError("raster CRS is missing")
            bounds = transform_bounds(
                dataset.crs,
                "EPSG:4326",
                *dataset.bounds,
                densify_pts=21,
            )
    except (OSError, ValueError, RasterioError) as error:
        raise PublicationArtifactError("publication raster metadata is unavailable") from error
    return (
        float(bounds[0]),
        float(bounds[1]),
        float(bounds[2]),
        float(bounds[3]),
    )


def _load_source_metadata(path: Path) -> _SourceMetadata:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        approval = payload.get("approval")
        if (
            not isinstance(approval, dict)
            or approval.get("gate") != "G2"
            or approval.get("status") != "approved"
        ):
            raise ValueError("manifest is not approved")
        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise ValueError("manifest assets must be a list")

        required_ids = {"before_red", "before_nir", "after_red", "after_nir"}
        selected: dict[str, dict[str, object]] = {}
        for candidate in assets:
            if not isinstance(candidate, dict):
                raise ValueError("manifest asset must be an object")
            logical_id = candidate.get("logical_id")
            if logical_id in required_ids:
                if not isinstance(logical_id, str) or logical_id in selected:
                    raise ValueError("manifest logical IDs must be unique")
                selected[logical_id] = candidate
        if set(selected) != required_ids:
            raise ValueError("manifest is missing a source date")

        acquired = {
            logical_id: date.fromisoformat(str(asset["acquired_on"]))
            for logical_id, asset in selected.items()
        }
        before_date = acquired["before_red"]
        after_date = acquired["after_red"]
        if acquired["before_nir"] != before_date or acquired["after_nir"] != after_date:
            raise ValueError("red and NIR source dates do not agree")
        if before_date > after_date:
            raise ValueError("source dates are reversed")

        organizations: set[str] = set()
        for asset in selected.values():
            source_assets = asset.get("source_assets")
            if not isinstance(source_assets, list) or not source_assets:
                raise ValueError("source attribution is missing")
            for source_asset in source_assets:
                if not isinstance(source_asset, dict):
                    raise ValueError("source attribution is invalid")
                organization = source_asset.get("organization")
                if not isinstance(organization, str) or not organization.strip():
                    raise ValueError("source attribution is invalid")
                organizations.add(organization.strip())
        if not organizations or not any("Copernicus" in value for value in organizations):
            raise ValueError("Copernicus attribution is missing")
        attribution = "包含经修改的 Copernicus Sentinel 数据；来源：" + "、".join(
            sorted(organizations)
        )
        if len(attribution) > 200:
            raise ValueError("source attribution is too long")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PublicationConfigurationError("approved source metadata is unavailable") from error
    return _SourceMetadata(
        before_date=before_date,
        after_date=after_date,
        attribution=attribution,
    )
