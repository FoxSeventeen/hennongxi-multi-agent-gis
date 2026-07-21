import type {
  TaskPublication,
  TaskTileLegendEntry,
  TaskTileMetadata,
} from "../../api/task-contract";

export type DisplayTileArtifactType =
  | "NDVI_BEFORE"
  | "NDVI_AFTER"
  | "NDVI_DIFFERENCE";

export interface MapDisplayLayer {
  readonly artifactType: DisplayTileArtifactType;
  readonly label: string;
  readonly periodLabel: string;
  readonly tileUrl: string;
  readonly units: string;
  readonly attribution: string;
  readonly legend: readonly TaskTileLegendEntry[];
}

export interface MapPresentation {
  readonly taskId: string;
  readonly attempt: number;
  readonly boundsWgs84: readonly [number, number, number, number];
  readonly layers: readonly MapDisplayLayer[];
}

export type MapPresentationResult =
  | { readonly status: "ready"; readonly presentation: MapPresentation }
  | { readonly status: "unavailable"; readonly message: string };

interface LayerDefinition {
  readonly artifactType: DisplayTileArtifactType;
  readonly label: string;
  readonly missingMessage: string;
}

const layerDefinitions: readonly LayerDefinition[] = [
  {
    artifactType: "NDVI_BEFORE",
    label: "前期 NDVI",
    missingMessage: "发布结果缺少前期 NDVI 图层。",
  },
  {
    artifactType: "NDVI_AFTER",
    label: "后期 NDVI",
    missingMessage: "发布结果缺少后期 NDVI 图层。",
  },
  {
    artifactType: "NDVI_DIFFERENCE",
    label: "NDVI 差值",
    missingMessage: "发布结果缺少 NDVI 差值图层。",
  },
];

export function buildMapPresentation(
  publication: TaskPublication,
  publisherBaseUrl: string,
): MapPresentationResult {
  const publisherOrigin = parsePublisherOrigin(publisherBaseUrl);
  if (publisherOrigin === null) {
    return { status: "unavailable", message: "Publisher 图层地址配置无效。" };
  }

  const layers: MapDisplayLayer[] = [];
  let commonBounds: TaskTileMetadata["boundsWgs84"] | null = null;
  for (const definition of layerDefinitions) {
    const resource = publication.resources.find(
      (candidate) => candidate.tileMetadata?.artifactType === definition.artifactType,
    );
    if (resource?.tileTemplate == null || resource.tileMetadata == null) {
      return { status: "unavailable", message: definition.missingMessage };
    }
    if (commonBounds === null) {
      commonBounds = resource.tileMetadata.boundsWgs84;
    } else if (!sameBounds(commonBounds, resource.tileMetadata.boundsWgs84)) {
      return { status: "unavailable", message: "发布图层的空间范围不一致。" };
    }
    layers.push({
      artifactType: definition.artifactType,
      label: definition.label,
      periodLabel: formatPeriod(resource.tileMetadata.startDate, resource.tileMetadata.endDate),
      tileUrl: `${publisherOrigin}${resource.tileTemplate}`,
      units: resource.tileMetadata.units,
      attribution: resource.tileMetadata.attribution,
      legend: resource.tileMetadata.legend,
    });
  }

  if (commonBounds === null) {
    return { status: "unavailable", message: "发布结果暂时没有可显示的地图图层。" };
  }
  return {
    status: "ready",
    presentation: {
      taskId: publication.taskId,
      attempt: publication.attempt,
      boundsWgs84: commonBounds,
      layers,
    },
  };
}

function parsePublisherOrigin(value: string): string | null {
  try {
    const parsed = new URL(value);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      parsed.username.length > 0 ||
      parsed.password.length > 0 ||
      parsed.search.length > 0 ||
      parsed.hash.length > 0
    ) {
      return null;
    }
    return parsed.origin;
  } catch {
    return null;
  }
}

function sameBounds(
  left: TaskTileMetadata["boundsWgs84"],
  right: TaskTileMetadata["boundsWgs84"],
): boolean {
  return left.every((coordinate, index) => coordinate === right[index]);
}

function formatPeriod(startDate: string, endDate: string): string {
  return startDate === endDate ? startDate : `${startDate} — ${endDate}`;
}
