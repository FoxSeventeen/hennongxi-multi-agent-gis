import type { components } from "./schema.generated";

export type AgentName = components["schemas"]["AgentName"];
export type PlanSource = components["schemas"]["PlanSource"];
export type PlanStepKind = components["schemas"]["PlanStepKind"];
export type StepStatus = components["schemas"]["StepStatus"];
export type TaskStatus = components["schemas"]["TaskStatus"];

type ArtifactRefWire = components["schemas"]["ArtifactRef"];
type AnalysisRunResultWire = components["schemas"]["AnalysisRunResult"];
type AreaStatisticsWire = components["schemas"]["AreaStatistics"];
type ErrorDetailWire = components["schemas"]["ErrorDetail"];
type ExecutionPlanWire = components["schemas"]["ExecutionPlan"];
type ModelCallRecordWire = components["schemas"]["ModelCallRecord"];
type PlanStepWire = components["schemas"]["PlanStep"];
type PublishedResourceWire = components["schemas"]["PublishedResource"];
type PublisherPublishResultWire = components["schemas"]["PublisherPublishResult"];
type QualityEvaluateResultWire = components["schemas"]["QualityEvaluateResult"];
type QualityMetricsWire = components["schemas"]["QualityMetrics"];
type QualityThresholdsWire = components["schemas"]["QualityThresholds"];
type StructuredErrorWire = components["schemas"]["StructuredError"];
type TaskEventWire = components["schemas"]["TaskEvent"];
type TaskResponseWire = components["schemas"]["TaskResponse"];
type TaskStepWire = components["schemas"]["TaskStep"];
type TileLegendEntryWire = components["schemas"]["TileLegendEntry"];
type TileMetadataWire = components["schemas"]["TileMetadata"];

export interface TimelineErrorDetail {
  readonly field: string | null;
  readonly reason: string;
}

export interface TimelineError {
  readonly code: components["schemas"]["ErrorCode"];
  readonly message: string;
  readonly retryable: boolean;
  readonly details: readonly TimelineErrorDetail[];
}

export interface ModelCallEvidence {
  readonly model: string;
  readonly startedAt: string;
  readonly durationMs: number;
  readonly status: components["schemas"]["ModelCallStatus"];
  readonly inputTokens: number | null;
  readonly outputTokens: number | null;
  readonly responseSha256: string | null;
  readonly errorCode: string | null;
}

export interface TaskPlanStep {
  readonly stepId: string;
  readonly kind: PlanStepKind;
  readonly agent: AgentName;
  readonly order: number;
  readonly title: string;
  readonly dependsOn: readonly string[];
}

export interface TaskPlan {
  readonly planId: string;
  readonly source: PlanSource;
  readonly createdAt: string;
  readonly modelCall: ModelCallEvidence | null;
  readonly steps: readonly TaskPlanStep[];
}

export interface TaskStep {
  readonly stepId: string;
  readonly kind: PlanStepKind;
  readonly agent: AgentName;
  readonly attempt: number;
  readonly status: StepStatus;
  readonly progress: number;
  readonly startedAt: string | null;
  readonly completedAt: string | null;
  readonly elapsedMs: number | null;
  readonly error: TimelineError | null;
}

export interface TaskTileLegendEntry {
  readonly value: number;
  readonly label: string;
  readonly color: string;
}

export interface TaskTileMetadata {
  readonly artifactType: components["schemas"]["TileArtifactType"];
  readonly boundsWgs84: readonly [number, number, number, number];
  readonly startDate: string;
  readonly endDate: string;
  readonly units: string;
  readonly attribution: string;
  readonly legend: readonly TaskTileLegendEntry[];
}

export interface TaskPublishedResource {
  readonly artifactId: string;
  readonly tileTemplate: string | null;
  readonly downloadPath: string | null;
  readonly tileMetadata: TaskTileMetadata | null;
}

export interface TaskPublication {
  readonly taskId: string;
  readonly attempt: number;
  readonly correlationId: string;
  readonly resources: readonly TaskPublishedResource[];
  readonly report: TaskReport;
}

export interface TaskReport {
  readonly artifactId: string;
  readonly createdAt: string;
  readonly checksumSha256: string;
  readonly byteSize: number;
}

export interface TaskAreaStatistics {
  readonly increaseHectares: number;
  readonly stableHectares: number;
  readonly decreaseHectares: number;
  readonly validHectares: number;
}

export interface TaskAnalysisResult {
  readonly taskId: string;
  readonly attempt: number;
  readonly correlationId: string;
  readonly statistics: TaskAreaStatistics;
  readonly elapsedMs: number;
}

export interface TaskQualityThresholds {
  readonly minimumWatershedCoverageRatio: number;
  readonly minimumValidPixelRatio: number;
}

export interface TaskQualityResult {
  readonly taskId: string;
  readonly attempt: number;
  readonly correlationId: string;
  readonly coverageRatio: number;
  readonly validPixelRatio: number;
  readonly outputComplete: boolean;
  readonly elapsedMs: number;
  readonly thresholds: TaskQualityThresholds;
  readonly conclusion: components["schemas"]["QualityConclusion"];
  readonly passed: boolean;
  readonly evidence: readonly string[];
}

export interface TaskSnapshot {
  readonly taskId: string;
  readonly query: string;
  readonly status: TaskStatus;
  readonly progress: number;
  readonly currentAttempt: number;
  readonly correlationId: string;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly plan: TaskPlan | null;
  readonly steps: readonly TaskStep[];
  readonly lastError: TimelineError | null;
  readonly analysis: TaskAnalysisResult | null;
  readonly quality: TaskQualityResult | null;
  readonly publication: TaskPublication | null;
}

export interface TaskEvent {
  readonly sequence: number;
  readonly taskId: string;
  readonly stepId: string;
  readonly attempt: number;
  readonly correlationId: string;
  readonly agent: AgentName;
  readonly status: TaskStatus;
  readonly progress: number;
  readonly message: string;
  readonly elapsedMs: number;
  readonly occurredAt: string;
  readonly error: TimelineError | null;
}

const agentNames = new Set<string>(["master", "data", "analysis", "quality", "publisher"]);
const artifactStatuses = new Set<string>(["STAGING", "COMPLETE", "FAILED"]);
const artifactTypes = new Set<string>([
  "DATA_MANIFEST",
  "WATERSHED_BOUNDARY",
  "NDVI_BEFORE",
  "NDVI_AFTER",
  "NDVI_DIFFERENCE",
  "CHANGE_CLASSIFICATION",
  "AREA_STATISTICS",
  "QUALITY_REPORT",
  "PDF_REPORT",
]);
const errorCodes = new Set<string>([
  "VALIDATION_ERROR",
  "INVALID_PLAN",
  "TRANSITION_NOT_ALLOWED",
  "TASK_NOT_FOUND",
  "CONFLICT",
  "DEPENDENCY_UNAVAILABLE",
  "DATA_INVALID",
  "ANALYSIS_FAILED",
  "QUALITY_FAILED",
  "PUBLISHING_FAILED",
  "INTERNAL_ERROR",
]);
const modelCallStatuses = new Set<string>(["SUCCEEDED", "FAILED"]);
const planSources = new Set<string>(["REAL_LLM", "BUILTIN_RECOVERY"]);
const planStepKinds = new Set<string>([
  "prepare_data",
  "analyze_ndvi_change",
  "evaluate_quality",
  "publish_results",
]);
const stepStatuses = new Set<string>(["PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"]);
const taskStatuses = new Set<string>([
  "PENDING",
  "PLANNING",
  "DATA_PREPARING",
  "ANALYZING",
  "QUALITY_CHECKING",
  "PUBLISHING",
  "COMPLETED",
  "FAILED",
]);
const tileArtifactTypes = new Set<string>([
  "NDVI_BEFORE",
  "NDVI_AFTER",
  "NDVI_DIFFERENCE",
  "CHANGE_CLASSIFICATION",
]);
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const stepIdPattern = /^[a-z][a-z0-9_]{0,63}$/;
const sha256Pattern = /^[0-9a-f]{64}$/;

export function isTaskId(value: string): boolean {
  return uuidPattern.test(value);
}

export function parseTaskSnapshot(value: unknown, expectedTaskId: string): TaskSnapshot | null {
  if (
    !isTaskResponse(value) ||
    value.task_id !== expectedTaskId ||
    (value.analysis != null &&
      (value.analysis.task_id !== value.task_id ||
        value.analysis.attempt !== value.current_attempt ||
        value.analysis.correlation_id !== value.correlation_id)) ||
    (value.quality != null &&
      (value.quality.task_id !== value.task_id ||
        value.quality.attempt !== value.current_attempt ||
        value.quality.correlation_id !== value.correlation_id)) ||
    (value.publication != null &&
      (value.publication.task_id !== value.task_id ||
        value.publication.attempt !== value.current_attempt ||
        value.publication.correlation_id !== value.correlation_id))
  ) {
    return null;
  }
  return {
    taskId: value.task_id,
    query: value.query,
    status: value.status,
    progress: value.progress,
    currentAttempt: value.current_attempt,
    correlationId: value.correlation_id,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
    plan: value.plan == null ? null : mapPlan(value.plan),
    steps: value.steps.map(mapTaskStep),
    lastError: value.last_error == null ? null : mapError(value.last_error),
    analysis: value.analysis == null ? null : mapAnalysis(value.analysis),
    quality: value.quality == null ? null : mapQuality(value.quality),
    publication: value.publication == null ? null : mapPublication(value.publication),
  };
}

export function parseTaskEvent(value: unknown, expectedTaskId: string): TaskEvent | null {
  if (!isTaskEvent(value) || value.task_id !== expectedTaskId) {
    return null;
  }
  return {
    sequence: value.sequence,
    taskId: value.task_id,
    stepId: value.step_id,
    attempt: value.attempt,
    correlationId: value.correlation_id,
    agent: value.agent,
    status: value.status,
    progress: value.progress,
    message: value.message,
    elapsedMs: value.elapsed_ms,
    occurredAt: value.occurred_at,
    error: value.error == null ? null : mapError(value.error),
  };
}

function mapPlan(value: ExecutionPlanWire): TaskPlan {
  return {
    planId: value.plan_id,
    source: value.source,
    createdAt: value.created_at,
    modelCall: value.model_call == null ? null : mapModelCall(value.model_call),
    steps: value.steps.map(mapPlanStep),
  };
}

function mapModelCall(value: ModelCallRecordWire): ModelCallEvidence {
  return {
    model: value.model,
    startedAt: value.started_at,
    durationMs: value.duration_ms,
    status: value.status,
    inputTokens: value.input_tokens ?? null,
    outputTokens: value.output_tokens ?? null,
    responseSha256: value.response_sha256 ?? null,
    errorCode: value.error_code ?? null,
  };
}

function mapPlanStep(value: PlanStepWire): TaskPlanStep {
  return {
    stepId: value.step_id,
    kind: value.kind,
    agent: value.agent,
    order: value.order,
    title: value.title,
    dependsOn: value.depends_on,
  };
}

function mapTaskStep(value: TaskStepWire): TaskStep {
  return {
    stepId: value.step_id,
    kind: value.kind,
    agent: value.agent,
    attempt: value.attempt,
    status: value.status,
    progress: value.progress,
    startedAt: value.started_at ?? null,
    completedAt: value.completed_at ?? null,
    elapsedMs: value.elapsed_ms ?? null,
    error: value.error == null ? null : mapError(value.error),
  };
}

function mapPublication(value: PublisherPublishResultWire): TaskPublication {
  return {
    taskId: value.task_id,
    attempt: value.attempt,
    correlationId: value.correlation_id,
    resources: value.resources.map(mapPublishedResource),
    report: {
      artifactId: value.report.artifact_id,
      createdAt: value.report.created_at,
      checksumSha256: value.report.checksum_sha256 ?? "",
      byteSize: value.report.byte_size ?? 0,
    },
  };
}

function mapAnalysis(value: AnalysisRunResultWire): TaskAnalysisResult {
  return {
    taskId: value.task_id,
    attempt: value.attempt,
    correlationId: value.correlation_id,
    statistics: {
      increaseHectares: value.statistics.increase_hectares,
      stableHectares: value.statistics.stable_hectares,
      decreaseHectares: value.statistics.decrease_hectares,
      validHectares: value.statistics.valid_hectares,
    },
    elapsedMs: value.elapsed_ms,
  };
}

function mapQuality(value: QualityEvaluateResultWire): TaskQualityResult {
  return {
    taskId: value.task_id,
    attempt: value.attempt,
    correlationId: value.correlation_id,
    coverageRatio: value.metrics.coverage_ratio,
    validPixelRatio: value.metrics.valid_pixel_ratio,
    outputComplete: value.metrics.output_complete,
    elapsedMs: value.metrics.elapsed_ms,
    thresholds: {
      minimumWatershedCoverageRatio: value.metrics.thresholds.minimum_watershed_coverage_ratio,
      minimumValidPixelRatio: value.metrics.thresholds.minimum_valid_pixel_ratio,
    },
    conclusion: value.metrics.conclusion,
    passed: value.metrics.passed,
    evidence: value.metrics.evidence,
  };
}

function mapPublishedResource(value: PublishedResourceWire): TaskPublishedResource {
  return {
    artifactId: value.artifact_id,
    tileTemplate: value.tile_template ?? null,
    downloadPath: value.download_path ?? null,
    tileMetadata: value.tile_metadata == null ? null : mapTileMetadata(value.tile_metadata),
  };
}

function mapTileMetadata(value: TileMetadataWire): TaskTileMetadata {
  return {
    artifactType: value.artifact_type,
    boundsWgs84: value.bounds_wgs84,
    startDate: value.start_date,
    endDate: value.end_date,
    units: value.units,
    attribution: value.attribution,
    legend: value.legend.map((entry) => ({
      value: entry.value,
      label: entry.label,
      color: entry.color,
    })),
  };
}

function mapError(value: StructuredErrorWire): TimelineError {
  return {
    code: value.code,
    message: value.message,
    retryable: value.retryable,
    details: value.details.map((detail) => ({
      field: detail.field ?? null,
      reason: detail.reason,
    })),
  };
}

function isTaskResponse(value: unknown): value is TaskResponseWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "task_id",
      "query",
      "status",
      "progress",
      "current_attempt",
      "correlation_id",
      "created_at",
      "updated_at",
      "plan",
      "steps",
      "artifacts",
      "last_error",
      "analysis",
      "quality",
      "publication",
    ]) &&
    hasSchemaVersion(value) &&
    isUuid(value["task_id"]) &&
    isBoundedText(value["query"], 2000) &&
    isTaskStatus(value["status"]) &&
    isIntegerBetween(value["progress"], 0, 100) &&
    isIntegerBetween(value["current_attempt"], 1, Number.MAX_SAFE_INTEGER) &&
    isUuid(value["correlation_id"]) &&
    isUtcDateTime(value["created_at"]) &&
    isUtcDateTime(value["updated_at"]) &&
    isOptionalNullable(value["plan"], isExecutionPlan) &&
    isArrayOf(value["steps"], isTaskStep) &&
    isArrayOf(value["artifacts"], isArtifactRef) &&
    isOptionalNullable(value["last_error"], isStructuredError) &&
    isOptionalNullable(value["analysis"], isAnalysisRunResult) &&
    isOptionalNullable(value["quality"], isQualityEvaluateResult) &&
    isOptionalNullable(value["publication"], isPublisherPublishResult)
  );
}

function isAnalysisRunResult(value: unknown): value is AnalysisRunResultWire {
  if (
    !isRecordWithKeys(value, [
      "schema_version",
      "task_id",
      "step_id",
      "attempt",
      "correlation_id",
      "artifacts",
      "statistics",
      "elapsed_ms",
    ]) ||
    !hasSchemaVersion(value) ||
    !isUuid(value["task_id"]) ||
    value["step_id"] !== "analyze_ndvi_change" ||
    !isIntegerBetween(value["attempt"], 1, Number.MAX_SAFE_INTEGER) ||
    !isUuid(value["correlation_id"]) ||
    !isArrayOf(value["artifacts"], isArtifactRef) ||
    !isAreaStatistics(value["statistics"]) ||
    !isNonNegativeInteger(value["elapsed_ms"])
  ) {
    return false;
  }
  const requiredTypes = new Set([
    "NDVI_BEFORE",
    "NDVI_AFTER",
    "NDVI_DIFFERENCE",
    "CHANGE_CLASSIFICATION",
    "AREA_STATISTICS",
  ]);
  const artifacts = value["artifacts"];
  return (
    artifacts.length === requiredTypes.size &&
    new Set(artifacts.map((artifact) => artifact.artifact_type)).size === requiredTypes.size &&
    artifacts.every(
      (artifact) =>
        artifact.task_id === value["task_id"] &&
        artifact.attempt === value["attempt"] &&
        artifact.status === "COMPLETE" &&
        requiredTypes.has(artifact.artifact_type),
    )
  );
}

function isAreaStatistics(value: unknown): value is AreaStatisticsWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "increase_hectares",
      "stable_hectares",
      "decrease_hectares",
      "valid_hectares",
    ]) &&
    hasSchemaVersion(value) &&
    isFiniteNumberAtLeast(value["increase_hectares"], 0) &&
    isFiniteNumberAtLeast(value["stable_hectares"], 0) &&
    isFiniteNumberAtLeast(value["decrease_hectares"], 0) &&
    isFiniteNumberAtLeast(value["valid_hectares"], Number.MIN_VALUE)
  );
}

function isQualityEvaluateResult(value: unknown): value is QualityEvaluateResultWire {
  if (
    !isRecordWithKeys(value, [
      "schema_version",
      "task_id",
      "step_id",
      "attempt",
      "correlation_id",
      "metrics",
      "artifact",
    ]) ||
    !hasSchemaVersion(value) ||
    !isUuid(value["task_id"]) ||
    value["step_id"] !== "evaluate_quality" ||
    !isIntegerBetween(value["attempt"], 1, Number.MAX_SAFE_INTEGER) ||
    !isUuid(value["correlation_id"]) ||
    !isQualityMetrics(value["metrics"]) ||
    !isArtifactRef(value["artifact"])
  ) {
    return false;
  }
  const artifact = value["artifact"];
  return (
    artifact.task_id === value["task_id"] &&
    artifact.attempt === value["attempt"] &&
    artifact.artifact_type === "QUALITY_REPORT" &&
    artifact.status === "COMPLETE" &&
    artifact.media_type === "application/json"
  );
}

function isQualityMetrics(value: unknown): value is QualityMetricsWire {
  if (
    !isRecordWithKeys(value, [
      "schema_version",
      "coverage_ratio",
      "valid_pixel_ratio",
      "output_complete",
      "elapsed_ms",
      "thresholds",
      "conclusion",
      "passed",
      "evidence",
    ]) ||
    !hasSchemaVersion(value) ||
    !isFiniteNumberBetween(value["coverage_ratio"], 0, 1) ||
    !isFiniteNumberBetween(value["valid_pixel_ratio"], 0, 1) ||
    typeof value["output_complete"] !== "boolean" ||
    !isNonNegativeInteger(value["elapsed_ms"]) ||
    !isQualityThresholds(value["thresholds"]) ||
    !["PASS", "WARN", "FAIL"].includes(String(value["conclusion"])) ||
    typeof value["passed"] !== "boolean" ||
    !isArrayOf(value["evidence"], (item): item is string => isBoundedText(item, 200)) ||
    value["evidence"].length < 4
  ) {
    return false;
  }
  const isPass = value["conclusion"] === "PASS";
  const thresholds = value["thresholds"];
  const gatesPass =
    value["coverage_ratio"] >= thresholds.minimum_watershed_coverage_ratio &&
    value["valid_pixel_ratio"] >= thresholds.minimum_valid_pixel_ratio &&
    value["output_complete"];
  return value["passed"] === isPass && (!isPass || gatesPass);
}

function isQualityThresholds(value: unknown): value is QualityThresholdsWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "minimum_watershed_coverage_ratio",
      "minimum_valid_pixel_ratio",
      "output_complete_required",
      "elapsed_minimum_ms",
    ]) &&
    hasSchemaVersion(value) &&
    isFiniteNumberBetween(value["minimum_watershed_coverage_ratio"], 0, 1) &&
    isFiniteNumberBetween(value["minimum_valid_pixel_ratio"], 0, 1) &&
    value["output_complete_required"] === true &&
    value["elapsed_minimum_ms"] === 0
  );
}

function isPublisherPublishResult(value: unknown): value is PublisherPublishResultWire {
  if (
    !isRecordWithKeys(value, [
      "schema_version",
      "task_id",
      "step_id",
      "attempt",
      "correlation_id",
      "resources",
      "report",
    ]) ||
    !hasSchemaVersion(value) ||
    !isUuid(value["task_id"]) ||
    value["step_id"] !== "publish_results" ||
    !isIntegerBetween(value["attempt"], 1, Number.MAX_SAFE_INTEGER) ||
    !isUuid(value["correlation_id"]) ||
    !isArrayOf(value["resources"], isPublishedResource) ||
    !isArtifactRef(value["report"])
  ) {
    return false;
  }

  const taskId = value["task_id"];
  const attempt = value["attempt"];
  const resources = value["resources"];
  const report = value["report"];
  const tileResources = resources.filter(
    (resource): resource is PublishedResourceWire & { readonly tile_metadata: TileMetadataWire } =>
      resource.tile_template != null && resource.tile_metadata != null,
  );
  const downloadResources = resources.filter((resource) => resource.download_path != null);
  const publishedTypes = new Set<string>(
    tileResources.map((resource) => resource.tile_metadata.artifact_type),
  );
  const downloadResource = downloadResources[0];

  return (
    report.task_id === taskId &&
    report.attempt === attempt &&
    report.artifact_type === "PDF_REPORT" &&
    report.status === "COMPLETE" &&
    report.media_type === "application/pdf" &&
    isSha256(report.checksum_sha256) &&
    isPositiveInteger(report.byte_size) &&
    resources.length === 5 &&
    tileResources.length === tileArtifactTypes.size &&
    publishedTypes.size === tileArtifactTypes.size &&
    [...tileArtifactTypes].every((artifactType) => publishedTypes.has(artifactType)) &&
    tileResources.every(
      (resource) =>
        resource.tile_template ===
        `/api/v1/tiles/${taskId}/${resource.tile_metadata.artifact_type}/{z}/{x}/{y}.png`,
    ) &&
    downloadResources.length === 1 &&
    downloadResource?.artifact_id === report.artifact_id &&
    downloadResource.download_path ===
      `/api/v1/tasks/${taskId}/artifacts/${report.artifact_id}/download`
  );
}

function isPublishedResource(value: unknown): value is PublishedResourceWire {
  if (
    !isRecordWithKeys(value, [
      "schema_version",
      "artifact_id",
      "tile_template",
      "download_path",
      "tile_metadata",
    ]) ||
    !hasSchemaVersion(value) ||
    !isUuid(value["artifact_id"]) ||
    !isOptionalNullable(value["tile_template"], isSafeApiPath) ||
    !isOptionalNullable(value["download_path"], isSafeApiPath) ||
    !isOptionalNullable(value["tile_metadata"], isTileMetadata)
  ) {
    return false;
  }
  const hasTile = value["tile_template"] != null;
  const hasDownload = value["download_path"] != null;
  return (hasTile || hasDownload) && hasTile === (value["tile_metadata"] != null);
}

function isTileMetadata(value: unknown): value is TileMetadataWire {
  if (
    !isRecordWithKeys(value, [
      "schema_version",
      "artifact_type",
      "bounds_wgs84",
      "start_date",
      "end_date",
      "units",
      "attribution",
      "legend",
    ]) ||
    !hasSchemaVersion(value) ||
    typeof value["artifact_type"] !== "string" ||
    !tileArtifactTypes.has(value["artifact_type"]) ||
    !isWgs84Bounds(value["bounds_wgs84"]) ||
    !isIsoDate(value["start_date"]) ||
    !isIsoDate(value["end_date"]) ||
    value["start_date"] > value["end_date"] ||
    !isBoundedText(value["units"], 200) ||
    !isBoundedText(value["attribution"], 200) ||
    !isArrayOf(value["legend"], isTileLegendEntry) ||
    value["legend"].length < 2 ||
    value["legend"].length > 12
  ) {
    return false;
  }
  return value["legend"].every(
    (entry, index, entries) => index === 0 || Number(entries[index - 1]?.value) < entry.value,
  );
}

function isTileLegendEntry(value: unknown): value is TileLegendEntryWire {
  return (
    isRecordWithKeys(value, ["schema_version", "value", "label", "color"]) &&
    hasSchemaVersion(value) &&
    typeof value["value"] === "number" &&
    Number.isFinite(value["value"]) &&
    isBoundedText(value["label"], 200) &&
    typeof value["color"] === "string" &&
    /^#[0-9a-f]{6}$/i.test(value["color"])
  );
}

function isTaskEvent(value: unknown): value is TaskEventWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "sequence",
      "task_id",
      "step_id",
      "attempt",
      "correlation_id",
      "agent",
      "status",
      "progress",
      "message",
      "elapsed_ms",
      "occurred_at",
      "error",
      "artifacts",
    ]) &&
    hasSchemaVersion(value) &&
    isIntegerBetween(value["sequence"], 1, Number.MAX_SAFE_INTEGER) &&
    isUuid(value["task_id"]) &&
    isStepId(value["step_id"]) &&
    isIntegerBetween(value["attempt"], 1, Number.MAX_SAFE_INTEGER) &&
    isUuid(value["correlation_id"]) &&
    isAgentName(value["agent"]) &&
    isTaskStatus(value["status"]) &&
    isIntegerBetween(value["progress"], 0, 100) &&
    isBoundedText(value["message"], 2000) &&
    isIntegerBetween(value["elapsed_ms"], 0, Number.MAX_SAFE_INTEGER) &&
    isUtcDateTime(value["occurred_at"]) &&
    isOptionalNullable(value["error"], isStructuredError) &&
    isArrayOf(value["artifacts"], isArtifactRef)
  );
}

function isExecutionPlan(value: unknown): value is ExecutionPlanWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "plan_id",
      "task_id",
      "source",
      "created_at",
      "model_call",
      "steps",
    ]) &&
    hasSchemaVersion(value) &&
    isUuid(value["plan_id"]) &&
    isUuid(value["task_id"]) &&
    typeof value["source"] === "string" &&
    planSources.has(value["source"]) &&
    isUtcDateTime(value["created_at"]) &&
    isOptionalNullable(value["model_call"], isModelCallRecord) &&
    isArrayOf(value["steps"], isPlanStep)
  );
}

function isModelCallRecord(value: unknown): value is ModelCallRecordWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "model",
      "started_at",
      "duration_ms",
      "status",
      "input_tokens",
      "output_tokens",
      "response_sha256",
      "error_code",
    ]) &&
    hasSchemaVersion(value) &&
    isBoundedText(value["model"], 200) &&
    isUtcDateTime(value["started_at"]) &&
    isIntegerBetween(value["duration_ms"], 0, Number.MAX_SAFE_INTEGER) &&
    typeof value["status"] === "string" &&
    modelCallStatuses.has(value["status"]) &&
    isOptionalNullable(value["input_tokens"], isNonNegativeInteger) &&
    isOptionalNullable(value["output_tokens"], isNonNegativeInteger) &&
    isOptionalNullable(value["response_sha256"], isSha256) &&
    isOptionalNullable(value["error_code"], (item) => isBoundedText(item, 200))
  );
}

function isPlanStep(value: unknown): value is PlanStepWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "step_id",
      "kind",
      "agent",
      "order",
      "title",
      "depends_on",
    ]) &&
    hasSchemaVersion(value) &&
    isStepId(value["step_id"]) &&
    typeof value["kind"] === "string" &&
    planStepKinds.has(value["kind"]) &&
    isAgentName(value["agent"]) &&
    isIntegerBetween(value["order"], 1, 4) &&
    isBoundedText(value["title"], 200) &&
    isArrayOf(value["depends_on"], isStepId)
  );
}

function isTaskStep(value: unknown): value is TaskStepWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "step_id",
      "kind",
      "agent",
      "attempt",
      "status",
      "progress",
      "started_at",
      "completed_at",
      "elapsed_ms",
      "error",
      "artifacts",
    ]) &&
    hasSchemaVersion(value) &&
    isStepId(value["step_id"]) &&
    typeof value["kind"] === "string" &&
    planStepKinds.has(value["kind"]) &&
    isAgentName(value["agent"]) &&
    isIntegerBetween(value["attempt"], 1, Number.MAX_SAFE_INTEGER) &&
    typeof value["status"] === "string" &&
    stepStatuses.has(value["status"]) &&
    isIntegerBetween(value["progress"], 0, 100) &&
    isOptionalNullable(value["started_at"], isUtcDateTime) &&
    isOptionalNullable(value["completed_at"], isUtcDateTime) &&
    isOptionalNullable(value["elapsed_ms"], isNonNegativeInteger) &&
    isOptionalNullable(value["error"], isStructuredError) &&
    isArrayOf(value["artifacts"], isArtifactRef)
  );
}

function isStructuredError(value: unknown): value is StructuredErrorWire {
  return (
    isRecordWithKeys(value, ["schema_version", "code", "message", "retryable", "details"]) &&
    hasSchemaVersion(value) &&
    typeof value["code"] === "string" &&
    errorCodes.has(value["code"]) &&
    isBoundedText(value["message"], 2000) &&
    typeof value["retryable"] === "boolean" &&
    isArrayOf(value["details"], isErrorDetail)
  );
}

function isErrorDetail(value: unknown): value is ErrorDetailWire {
  return (
    isRecordWithKeys(value, ["schema_version", "field", "reason"]) &&
    hasSchemaVersion(value) &&
    isOptionalNullable(value["field"], (item) => isBoundedText(item, 200)) &&
    isBoundedText(value["reason"], 2000)
  );
}

function isArtifactRef(value: unknown): value is ArtifactRefWire {
  return (
    isRecordWithKeys(value, [
      "schema_version",
      "artifact_id",
      "task_id",
      "attempt",
      "artifact_type",
      "status",
      "media_type",
      "created_at",
      "checksum_sha256",
      "byte_size",
    ]) &&
    hasSchemaVersion(value) &&
    isUuid(value["artifact_id"]) &&
    isUuid(value["task_id"]) &&
    isIntegerBetween(value["attempt"], 1, Number.MAX_SAFE_INTEGER) &&
    typeof value["artifact_type"] === "string" &&
    artifactTypes.has(value["artifact_type"]) &&
    typeof value["status"] === "string" &&
    artifactStatuses.has(value["status"]) &&
    isBoundedText(value["media_type"], 200) &&
    isUtcDateTime(value["created_at"]) &&
    isOptionalNullable(value["checksum_sha256"], isSha256) &&
    isOptionalNullable(value["byte_size"], isPositiveInteger)
  );
}

function isRecordWithKeys(value: unknown, allowedKeys: readonly string[]): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).every((key) => allowedKeys.includes(key))
  );
}

function hasSchemaVersion(value: Record<string, unknown>): boolean {
  return value["schema_version"] === "1.0";
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" && uuidPattern.test(value);
}

function isUtcDateTime(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /(?:Z|[+-]00:00)$/i.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function isIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return false;
  }
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

function isSafeApiPath(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.startsWith("/api/v1/") &&
    !value.includes("://") &&
    !value.includes("..") &&
    !value.includes("\\") &&
    Array.from(value).length <= 200
  );
}

function isWgs84Bounds(value: unknown): value is readonly [number, number, number, number] {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(Number.isFinite)) {
    return false;
  }
  const [west, south, east, north] = value as [number, number, number, number];
  return west >= -180 && west < east && east <= 180 && south >= -90 && south < north && north <= 90;
}

function isStepId(value: unknown): value is string {
  return typeof value === "string" && stepIdPattern.test(value);
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && sha256Pattern.test(value);
}

function isBoundedText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && Array.from(value).length <= maximum;
}

function isIntegerBetween(value: unknown, minimum: number, maximum: number): value is number {
  return Number.isSafeInteger(value) && Number(value) >= minimum && Number(value) <= maximum;
}

function isFiniteNumberBetween(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum && value <= maximum;
}

function isFiniteNumberAtLeast(value: unknown, minimum: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= minimum;
}

function isNonNegativeInteger(value: unknown): value is number {
  return isIntegerBetween(value, 0, Number.MAX_SAFE_INTEGER);
}

function isPositiveInteger(value: unknown): value is number {
  return isIntegerBetween(value, 1, Number.MAX_SAFE_INTEGER);
}

function isAgentName(value: unknown): value is AgentName {
  return typeof value === "string" && agentNames.has(value);
}

function isTaskStatus(value: unknown): value is TaskStatus {
  return typeof value === "string" && taskStatuses.has(value);
}

function isArrayOf<T>(value: unknown, predicate: (item: unknown) => item is T): value is readonly T[] {
  return Array.isArray(value) && value.every(predicate);
}

function isOptionalNullable<T>(
  value: unknown,
  predicate: (item: unknown) => item is T,
): value is T | null | undefined {
  return value === undefined || value === null || predicate(value);
}
