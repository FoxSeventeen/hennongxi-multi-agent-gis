import maplibregl, {
  type Map as MapLibreMap,
  type StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";

import type { TaskPublication } from "../api/task-contract";
import watershedBoundary from "../data/shennongxi-watershed.json";
import {
  buildMapPresentation,
  type DisplayTileArtifactType,
  type MapDisplayLayer,
  type MapPresentation,
} from "../features/map/map-model";

interface MapWorkspaceProps {
  readonly activeTaskId: string | null;
  readonly publication?: TaskPublication | null;
  readonly publisherBaseUrl?: string;
}

const displayLayerIds: Record<DisplayTileArtifactType, string> = {
  NDVI_BEFORE: "ndvi-before",
  NDVI_AFTER: "ndvi-after",
  NDVI_DIFFERENCE: "ndvi-difference",
};

const emptyMapStyle: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background",
      paint: { "background-color": "#dfe6dc" },
    },
  ],
};

export function MapWorkspace({
  activeTaskId,
  publication = null,
  publisherBaseUrl = "http://localhost:8004",
}: MapWorkspaceProps) {
  const presentationResult = useMemo(
    () =>
      publication === null ? null : buildMapPresentation(publication, publisherBaseUrl),
    [publication, publisherBaseUrl],
  );
  const stateLabel =
    presentationResult?.status === "ready"
      ? "图层已发布"
      : activeTaskId === null
        ? "等待任务"
        : "等待图层";

  return (
    <section className="map-workspace" aria-labelledby="map-workspace-title">
      <div className="map-heading">
        <div>
          <p className="section-kicker">研究区域</p>
          <h2 id="map-workspace-title">神农溪地图工作区</h2>
        </div>
        <p className="map-state">
          <span aria-hidden="true" />
          {stateLabel}
        </p>
      </div>

      {presentationResult === null ? (
        <MapPlaceholder activeTaskId={activeTaskId} />
      ) : presentationResult.status === "unavailable" ? (
        <MapUnavailable message={presentationResult.message} />
      ) : (
        <PublishedMap presentation={presentationResult.presentation} />
      )}
    </section>
  );
}

function PublishedMap({ presentation }: { readonly presentation: MapPresentation }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const mapLoadedRef = useRef(false);
  const activeTypeRef = useRef<DisplayTileArtifactType>("NDVI_DIFFERENCE");
  const [activeType, setActiveType] = useState<DisplayTileArtifactType>("NDVI_DIFFERENCE");
  const [loadState, setLoadState] = useState<"loading" | "ready">("loading");
  const [problem, setProblem] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const activeLayer = requireLayer(presentation, activeType);
  activeTypeRef.current = activeType;

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) {
      return;
    }
    let disposed = false;
    setLoadState("loading");
    setProblem(null);
    mapLoadedRef.current = false;

    try {
      const [west, south, east, north] = presentation.boundsWgs84;
      const map = new maplibregl.Map({
        container,
        style: emptyMapStyle,
        bounds: [
          [west, south],
          [east, north],
        ],
        fitBoundsOptions: { padding: 42, maxZoom: 12 },
      });
      mapRef.current = map;
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      map.on("load", () => {
        if (disposed) {
          return;
        }
        addPublishedLayers(map, presentation, activeTypeRef.current);
        map.fitBounds(
          [
            [west, south],
            [east, north],
          ],
          { padding: 42, maxZoom: 12, duration: 0 },
        );
        mapLoadedRef.current = true;
        setLoadState("ready");
      });
      map.on("error", () => {
        if (!disposed) {
          setProblem("当前图层瓦片加载失败。可切换其他图层，或重新加载地图图层。");
        }
      });
    } catch {
      setProblem("地图初始化失败。请重新加载地图图层。");
    }

    return () => {
      disposed = true;
      mapLoadedRef.current = false;
      const map = mapRef.current;
      mapRef.current = null;
      map?.remove();
    };
  }, [presentation, revision]);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null || !mapLoadedRef.current) {
      return;
    }
    for (const [artifactType, layerId] of Object.entries(displayLayerIds)) {
      map.setLayoutProperty(
        layerId,
        "visibility",
        artifactType === activeType ? "visible" : "none",
      );
    }
    setProblem(null);
  }, [activeType]);

  return (
    <div className="map-canvas map-canvas-live">
      <div
        ref={containerRef}
        className="maplibre-surface"
        role="img"
        aria-label={`神农溪完整流域边界与${activeLayer.label}图层，观测日期 ${activeLayer.periodLabel}`}
      />
      {loadState === "loading" ? (
        <div className="map-loading" role="status" aria-live="polite">
          <span aria-hidden="true" />
          正在准备地图图层…
        </div>
      ) : null}
      {problem === null ? null : (
        <div className="map-problem" role="alert">
          <p>{problem}</p>
          <button
            className="map-reload-button"
            type="button"
            onClick={() => {
              setRevision((current) => current + 1);
            }}
          >
            重新加载地图图层
          </button>
        </div>
      )}
      <aside className="map-layer-panel" aria-label="地图图层与图例">
        <div className="map-layer-controls" role="group" aria-label="NDVI 图层">
          {presentation.layers.map((layer) => (
            <button
              key={layer.artifactType}
              type="button"
              aria-pressed={layer.artifactType === activeType}
              onClick={() => {
                setActiveType(layer.artifactType);
              }}
            >
              {layer.label}
            </button>
          ))}
        </div>
        <div className="map-layer-metadata" aria-live="polite">
          <div>
            <span>观测日期</span>
            <strong>{activeLayer.periodLabel}</strong>
          </div>
          <div>
            <span>数值单位</span>
            <strong>{activeLayer.units}</strong>
          </div>
        </div>
        <div className="map-boundary-note">
          <span aria-hidden="true" />
          流域边界始终显示
        </div>
        <ul className="map-legend" aria-label={`${activeLayer.label}图例`}>
          {activeLayer.legend.map((entry) => (
            <li key={`${String(entry.value)}-${entry.label}`}>
              <span aria-hidden="true" style={{ backgroundColor: entry.color }} />
              <strong>{entry.label}</strong>
              <small>{entry.value}</small>
            </li>
          ))}
        </ul>
        <p className="map-attribution">{activeLayer.attribution}</p>
      </aside>
    </div>
  );
}

function addPublishedLayers(
  map: MapLibreMap,
  presentation: MapPresentation,
  activeType: DisplayTileArtifactType,
): void {
  const [west, south, east, north] = presentation.boundsWgs84;
  for (const layer of presentation.layers) {
    const layerId = displayLayerIds[layer.artifactType];
    map.addSource(`${layerId}-source`, {
      type: "raster",
      tiles: [layer.tileUrl],
      tileSize: 256,
      bounds: [west, south, east, north],
      attribution: layer.attribution,
    });
    map.addLayer({
      id: layerId,
      type: "raster",
      source: `${layerId}-source`,
      layout: { visibility: layer.artifactType === activeType ? "visible" : "none" },
      paint: { "raster-opacity": 0.84, "raster-fade-duration": 0 },
    });
  }
  map.addSource("watershed-boundary", {
    type: "geojson",
    data: watershedBoundary,
  });
  map.addLayer({
    id: "watershed-fill",
    type: "fill",
    source: "watershed-boundary",
    paint: { "fill-color": "#f8f7f1", "fill-opacity": 0.08 },
  });
  map.addLayer({
    id: "watershed-outline",
    type: "line",
    source: "watershed-boundary",
    paint: { "line-color": "#f8f7f1", "line-width": 2.4, "line-opacity": 0.96 },
  });
}

function requireLayer(
  presentation: MapPresentation,
  artifactType: DisplayTileArtifactType,
): MapDisplayLayer {
  const layer = presentation.layers.find((candidate) => candidate.artifactType === artifactType);
  if (layer === undefined) {
    throw new Error("map presentation is missing an approved NDVI layer");
  }
  return layer;
}

function MapUnavailable({ message }: { readonly message: string }) {
  return (
    <div className="map-canvas map-placeholder-canvas">
      <div className="map-empty-state" role="alert">
        <p>地图图层暂不可用</p>
        <span>{message}任务与 Agent 时间线仍可继续使用。</span>
      </div>
    </div>
  );
}

function MapPlaceholder({ activeTaskId }: { readonly activeTaskId: string | null }) {
  return (
    <div className="map-canvas map-placeholder-canvas">
      <div className="map-placeholder-boundary" aria-hidden="true" />
      <div className="map-location-label" aria-hidden="true">
        <span>31°19′N</span>
        <strong>神农溪流域</strong>
        <span>110°19′E</span>
      </div>
      <div className="map-empty-state">
        <p>{activeTaskId === null ? "地图已就位" : "等待分析图层"}</p>
        <span>
          {activeTaskId === null
            ? "任务完成后，将自动显示完整流域边界与 NDVI 图层。"
            : `任务 ${activeTaskId.slice(0, 8)} 正在执行，发布完成后将自动加载图层。`}
        </span>
      </div>
    </div>
  );
}
