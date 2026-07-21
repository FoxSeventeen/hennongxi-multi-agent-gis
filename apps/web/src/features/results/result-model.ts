import type {
  ModelCallEvidence,
  PlanSource,
  TaskAreaStatistics,
  TaskPublishedResource,
  TaskQualityResult,
  TaskSnapshot,
  TaskTileMetadata,
} from "../../api/task-contract";

export interface ResultPlanningEvidence {
  readonly source: PlanSource;
  readonly label: string;
  readonly isAcceptanceEvidence: boolean;
  readonly model: string | null;
  readonly status: ModelCallEvidence["status"] | null;
  readonly durationMs: number | null;
  readonly inputTokens: number | null;
  readonly outputTokens: number | null;
  readonly responseSha256: string | null;
  readonly errorCode: string | null;
}

export interface ResultReport {
  readonly url: string;
  readonly byteSize: number;
  readonly checksumSha256: string;
}

export interface ResultPresentation {
  readonly taskId: string;
  readonly attempt: number;
  readonly beforeDate: string;
  readonly afterDate: string;
  readonly units: "公顷";
  readonly statistics: TaskAreaStatistics;
  readonly quality: TaskQualityResult;
  readonly planning: ResultPlanningEvidence;
  readonly report: ResultReport;
}

export type ResultPresentationResult =
  | { readonly status: "ready"; readonly presentation: ResultPresentation }
  | { readonly status: "incomplete"; readonly message: string }
  | { readonly status: "unavailable"; readonly message: string };

export function buildResultPresentation(
  snapshot: TaskSnapshot,
  publisherBaseUrl: string,
): ResultPresentationResult {
  if (snapshot.status !== "COMPLETED" || snapshot.progress !== 100) {
    return { status: "incomplete", message: "本次执行未完成，不展示完整成果。" };
  }
  const { analysis, quality, publication, plan } = snapshot;
  if (analysis === null || quality === null || publication === null || plan === null) {
    return { status: "unavailable", message: "完整成果证据尚未齐备。" };
  }
  if (
    !sameCurrentAttempt(snapshot, analysis) ||
    !sameCurrentAttempt(snapshot, quality) ||
    !sameCurrentAttempt(snapshot, publication)
  ) {
    return { status: "unavailable", message: "成果与当前任务尝试不一致。" };
  }
  if (
    quality.conclusion !== "PASS" ||
    !quality.passed ||
    !quality.outputComplete ||
    quality.elapsedMs !== analysis.elapsedMs
  ) {
    return { status: "unavailable", message: "质量证据未完整通过，不能展示完整成果。" };
  }

  const periods = extractPeriods(publication.resources);
  if (periods === null) {
    return { status: "unavailable", message: "发布结果的观测日期不一致。" };
  }
  const publisherOrigin = parsePublisherOrigin(publisherBaseUrl);
  if (publisherOrigin === null) {
    return { status: "unavailable", message: "报告下载地址配置无效。" };
  }
  const expectedDownloadPath =
    `/api/v1/tasks/${snapshot.taskId}/artifacts/${publication.report.artifactId}/download`;
  const reportResource = publication.resources.find(
    (resource) => resource.artifactId === publication.report.artifactId,
  );
  if (
    reportResource?.downloadPath !== expectedDownloadPath ||
    reportResource.tileTemplate !== null ||
    reportResource.tileMetadata !== null
  ) {
    return { status: "unavailable", message: "报告下载资源与当前任务不一致。" };
  }

  return {
    status: "ready",
    presentation: {
      taskId: snapshot.taskId,
      attempt: snapshot.currentAttempt,
      beforeDate: periods.beforeDate,
      afterDate: periods.afterDate,
      units: "公顷",
      statistics: analysis.statistics,
      quality,
      planning: planningEvidence(plan.source, plan.modelCall),
      report: {
        url: `${publisherOrigin}${expectedDownloadPath}`,
        byteSize: publication.report.byteSize,
        checksumSha256: publication.report.checksumSha256,
      },
    },
  };
}

function sameCurrentAttempt(
  snapshot: TaskSnapshot,
  value: { readonly taskId: string; readonly attempt: number; readonly correlationId: string },
): boolean {
  return (
    value.taskId === snapshot.taskId &&
    value.attempt === snapshot.currentAttempt &&
    value.correlationId === snapshot.correlationId
  );
}

function extractPeriods(
  resources: readonly TaskPublishedResource[],
): { readonly beforeDate: string; readonly afterDate: string } | null {
  const before = findMetadata(resources, "NDVI_BEFORE");
  const after = findMetadata(resources, "NDVI_AFTER");
  const difference = findMetadata(resources, "NDVI_DIFFERENCE");
  const classification = findMetadata(resources, "CHANGE_CLASSIFICATION");
  if (
    before === null ||
    after === null ||
    difference === null ||
    classification === null ||
    before.startDate !== before.endDate ||
    after.startDate !== after.endDate ||
    difference.startDate !== before.startDate ||
    difference.endDate !== after.endDate ||
    classification.startDate !== before.startDate ||
    classification.endDate !== after.endDate
  ) {
    return null;
  }
  return { beforeDate: before.startDate, afterDate: after.startDate };
}

function findMetadata(
  resources: readonly TaskPublishedResource[],
  artifactType: TaskTileMetadata["artifactType"],
): TaskTileMetadata | null {
  return (
    resources.find((resource) => resource.tileMetadata?.artifactType === artifactType)
      ?.tileMetadata ?? null
  );
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

function planningEvidence(
  source: PlanSource,
  modelCall: ModelCallEvidence | null,
): ResultPlanningEvidence {
  return {
    source,
    label: source === "REAL_LLM" ? "真实大模型规划" : "内置恢复计划",
    isAcceptanceEvidence: source === "REAL_LLM" && modelCall?.status === "SUCCEEDED",
    model: modelCall?.model ?? null,
    status: modelCall?.status ?? null,
    durationMs: modelCall?.durationMs ?? null,
    inputTokens: modelCall?.inputTokens ?? null,
    outputTokens: modelCall?.outputTokens ?? null,
    responseSha256: modelCall?.responseSha256 ?? null,
    errorCode: modelCall?.errorCode ?? null,
  };
}
