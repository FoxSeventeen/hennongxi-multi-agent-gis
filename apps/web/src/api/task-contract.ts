import type { components } from "./schema.generated";

export type AgentName = components["schemas"]["AgentName"];
export type PlanSource = components["schemas"]["PlanSource"];
export type PlanStepKind = components["schemas"]["PlanStepKind"];
export type StepStatus = components["schemas"]["StepStatus"];
export type TaskStatus = components["schemas"]["TaskStatus"];

type ArtifactRefWire = components["schemas"]["ArtifactRef"];
type ErrorDetailWire = components["schemas"]["ErrorDetail"];
type ExecutionPlanWire = components["schemas"]["ExecutionPlan"];
type ModelCallRecordWire = components["schemas"]["ModelCallRecord"];
type PlanStepWire = components["schemas"]["PlanStep"];
type StructuredErrorWire = components["schemas"]["StructuredError"];
type TaskEventWire = components["schemas"]["TaskEvent"];
type TaskResponseWire = components["schemas"]["TaskResponse"];
type TaskStepWire = components["schemas"]["TaskStep"];

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
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const stepIdPattern = /^[a-z][a-z0-9_]{0,63}$/;
const sha256Pattern = /^[0-9a-f]{64}$/;

export function isTaskId(value: string): boolean {
  return uuidPattern.test(value);
}

export function parseTaskSnapshot(value: unknown, expectedTaskId: string): TaskSnapshot | null {
  if (!isTaskResponse(value) || value.task_id !== expectedTaskId) {
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
    isOptionalNullable(value["last_error"], isStructuredError)
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
