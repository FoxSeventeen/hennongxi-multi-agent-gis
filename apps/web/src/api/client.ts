import type { components } from "./schema.generated";
import { consumeEventStream } from "./sse";
import {
  isTaskId,
  parseTaskEvent,
  parseTaskSnapshot,
  type TaskEvent,
  type TaskSnapshot,
} from "./task-contract";

type ErrorCode = components["schemas"]["ErrorCode"];
type HealthState = components["schemas"]["HealthState"];
type ReadinessBlocker = components["schemas"]["ReadinessBlocker"];
type ServiceName = components["schemas"]["ServiceName"];
type TaskStatus = components["schemas"]["TaskStatus"];

type CreateTaskRequestWire = components["schemas"]["CreateTaskRequest"];
type ErrorResponseWire = components["schemas"]["ErrorResponse"];
type HealthResponseWire = components["schemas"]["HealthResponse"];
type ReadinessResponseWire = components["schemas"]["ReadinessResponse"];
type TaskAcceptedResponseWire = components["schemas"]["TaskAcceptedResponse"];

export interface ErrorDetail {
  readonly field: string | null;
  readonly reason: string;
}

export interface ServiceHealth {
  readonly service: ServiceName;
  readonly state: HealthState;
  readonly checkedAt: string;
  readonly message: string | null;
}

export interface AggregateHealth {
  readonly state: HealthState;
  readonly checkedAt: string;
  readonly services: readonly ServiceHealth[];
}

export interface ReadinessSnapshot {
  readonly ready: boolean;
  readonly llmConfigured: boolean;
  readonly dataConfigured: boolean;
  readonly blockers: readonly ReadinessBlocker[];
  readonly messages: readonly string[];
  readonly health: AggregateHealth;
}

export interface AcceptedTask {
  readonly taskId: string;
  readonly status: TaskStatus;
  readonly createdAt: string;
}

export class MasterApiError extends Error {
  readonly code: ErrorCode;
  readonly retryable: boolean;
  readonly details: readonly ErrorDetail[];

  constructor(options: {
    readonly code: ErrorCode;
    readonly message: string;
    readonly retryable: boolean;
    readonly details?: readonly ErrorDetail[];
  }) {
    super(options.message);
    this.name = "MasterApiError";
    this.code = options.code;
    this.retryable = options.retryable;
    this.details = options.details ?? [];
  }
}

export interface MasterClient {
  getReadiness(): Promise<ReadinessSnapshot>;
  createTask(query: string): Promise<AcceptedTask>;
  getTask(taskId: string): Promise<TaskSnapshot>;
  streamTaskEvents(taskId: string, options: StreamTaskEventsOptions): Promise<void>;
}

export interface StreamTaskEventsOptions {
  readonly afterSequence: number;
  readonly signal: AbortSignal;
  readonly onEvent: (event: TaskEvent) => void;
}

interface MasterClientOptions {
  readonly baseUrl: string;
  readonly fetcher?: typeof fetch;
}

const healthStates = new Set<string>(["HEALTHY", "DEGRADED", "UNAVAILABLE"]);
const readinessBlockers = new Set<string>([
  "LLM_NOT_CONFIGURED",
  "DATA_NOT_CONFIGURED",
  "DEPENDENCY_UNAVAILABLE",
]);
const serviceNames = new Set<string>([
  "master",
  "data",
  "analysis",
  "quality",
  "publisher",
  "postgis",
  "redis",
]);
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

export function createMasterClient(options: MasterClientOptions): MasterClient {
  const baseUrl = options.baseUrl.replace(/\/+$/, "");
  const fetcher = options.fetcher ?? fetch;

  return {
    async getReadiness(): Promise<ReadinessSnapshot> {
      const [readinessPayload, healthPayload] = await Promise.all([
        requestJson(fetcher, `${baseUrl}/api/v1/config/readiness`, {
          headers: { accept: "application/json" },
        }),
        requestJson(fetcher, `${baseUrl}/api/v1/health`, {
          headers: { accept: "application/json" },
        }),
      ]);

      if (!isReadinessResponse(readinessPayload) || !isHealthResponse(healthPayload)) {
        throw invalidResponseError();
      }

      return {
        ready: readinessPayload.ready,
        llmConfigured: readinessPayload.llm_configured,
        dataConfigured: readinessPayload.data_configured,
        blockers: readinessPayload.blockers,
        messages: readinessPayload.messages,
        health: {
          state: healthPayload.state,
          checkedAt: healthPayload.checked_at,
          services: healthPayload.services.map((service) => ({
            service: service.service,
            state: service.state,
            checkedAt: service.checked_at,
            message: service.message ?? null,
          })),
        },
      };
    },

    async createTask(query: string): Promise<AcceptedTask> {
      const normalizedQuery = query.trim();
      if (normalizedQuery.length === 0 || Array.from(normalizedQuery).length > 2000) {
        throw new MasterApiError({
          code: "VALIDATION_ERROR",
          message: "任务描述必须包含 1 至 2000 个字符。",
          retryable: false,
        });
      }

      const requestBody = {
        schema_version: "1.0",
        query: normalizedQuery,
      } satisfies CreateTaskRequestWire;
      const payload = await requestJson(fetcher, `${baseUrl}/api/v1/tasks`, {
        method: "POST",
        headers: {
          accept: "application/json",
          "content-type": "application/json",
        },
        body: JSON.stringify(requestBody),
      });

      if (!isTaskAcceptedResponse(payload)) {
        throw invalidResponseError();
      }

      return {
        taskId: payload.task_id,
        status: payload.status,
        createdAt: payload.created_at,
      };
    },

    async getTask(taskId: string): Promise<TaskSnapshot> {
      requireTaskId(taskId);
      const payload = await requestJson(fetcher, `${baseUrl}/api/v1/tasks/${taskId}`, {
        headers: { accept: "application/json" },
      });
      const snapshot = parseTaskSnapshot(payload, taskId);
      if (snapshot === null) {
        throw invalidResponseError();
      }
      return snapshot;
    },

    async streamTaskEvents(taskId: string, streamOptions: StreamTaskEventsOptions): Promise<void> {
      requireTaskId(taskId);
      if (!Number.isSafeInteger(streamOptions.afterSequence) || streamOptions.afterSequence < 0) {
        throw new MasterApiError({
          code: "VALIDATION_ERROR",
          message: "任务事件游标无效。",
          retryable: false,
        });
      }

      const headers: Record<string, string> = { accept: "text/event-stream" };
      if (streamOptions.afterSequence > 0) {
        headers["Last-Event-ID"] = String(streamOptions.afterSequence);
      }
      const response = await requestResponse(fetcher, `${baseUrl}/api/v1/tasks/${taskId}/events`, {
        method: "GET",
        headers,
        signal: streamOptions.signal,
      });
      if (!response.ok) {
        const payload = await readJson(response);
        throw responseError(response, payload);
      }
      if (!response.headers.get("content-type")?.toLowerCase().startsWith("text/event-stream")) {
        throw invalidResponseError();
      }

      try {
        await consumeEventStream(response, (sequence, payload) => {
          const event = parseTaskEvent(payload, taskId);
          if (event?.sequence !== sequence) {
            throw invalidResponseError();
          }
          streamOptions.onEvent(event);
        });
      } catch (reason: unknown) {
        if (reason instanceof MasterApiError || streamOptions.signal.aborted) {
          throw reason;
        }
        throw invalidResponseError();
      }
    },
  };
}

async function requestJson(
  fetcher: typeof fetch,
  url: string,
  init: RequestInit,
): Promise<unknown> {
  const response = await requestResponse(fetcher, url, init);
  const payload = await readJson(response);
  if (!response.ok) {
    throw responseError(response, payload);
  }
  return payload;
}

async function requestResponse(
  fetcher: typeof fetch,
  url: string,
  init: RequestInit,
): Promise<Response> {
  try {
    return await fetcher(url, init);
  } catch (reason: unknown) {
    if (init.signal?.aborted) {
      throw reason;
    }
    throw new MasterApiError({
      code: "DEPENDENCY_UNAVAILABLE",
      message: "无法连接 Master 服务，请稍后重试。",
      retryable: true,
    });
  }
}

function responseError(response: Response, payload: unknown): MasterApiError {
  if (isErrorResponse(payload)) {
    return new MasterApiError({
      code: payload.error.code,
      message: payload.error.message,
      retryable: payload.error.retryable,
      details: payload.error.details.map((detail) => ({
        field: detail.field ?? null,
        reason: detail.reason,
      })),
    });
  }
  return new MasterApiError({
    code: response.status >= 500 ? "DEPENDENCY_UNAVAILABLE" : "INTERNAL_ERROR",
    message: `Master 服务返回异常（HTTP ${String(response.status)}）。`,
    retryable: response.status >= 500,
  });
}

function requireTaskId(taskId: string): void {
  if (!isTaskId(taskId)) {
    throw new MasterApiError({
      code: "VALIDATION_ERROR",
      message: "任务编号格式无效。",
      retryable: false,
    });
  }
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw invalidResponseError();
  }
}

function invalidResponseError(): MasterApiError {
  return new MasterApiError({
    code: "INTERNAL_ERROR",
    message: "Master 服务返回了无法识别的数据。",
    retryable: true,
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasSchemaVersion(value: Record<string, unknown>): boolean {
  return value["schema_version"] === "1.0";
}

function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isReadinessBlockerArray(value: unknown): value is readonly ReadinessBlocker[] {
  return isStringArray(value) && value.every((item) => readinessBlockers.has(item));
}

function isReadinessResponse(value: unknown): value is ReadinessResponseWire {
  return (
    isRecord(value) &&
    hasSchemaVersion(value) &&
    typeof value["ready"] === "boolean" &&
    typeof value["llm_configured"] === "boolean" &&
    typeof value["data_configured"] === "boolean" &&
    isReadinessBlockerArray(value["blockers"]) &&
    isStringArray(value["messages"])
  );
}

function isHealthResponse(value: unknown): value is HealthResponseWire {
  return (
    isRecord(value) &&
    hasSchemaVersion(value) &&
    typeof value["state"] === "string" &&
    healthStates.has(value["state"]) &&
    typeof value["checked_at"] === "string" &&
    Array.isArray(value["services"]) &&
    value["services"].every(isServiceHealth)
  );
}

function isServiceHealth(value: unknown): value is components["schemas"]["ServiceHealth"] {
  return (
    isRecord(value) &&
    hasSchemaVersion(value) &&
    typeof value["service"] === "string" &&
    serviceNames.has(value["service"]) &&
    typeof value["state"] === "string" &&
    healthStates.has(value["state"]) &&
    typeof value["checked_at"] === "string" &&
    (value["message"] === undefined ||
      value["message"] === null ||
      typeof value["message"] === "string")
  );
}

function isTaskAcceptedResponse(value: unknown): value is TaskAcceptedResponseWire {
  return (
    isRecord(value) &&
    hasSchemaVersion(value) &&
    typeof value["task_id"] === "string" &&
    typeof value["status"] === "string" &&
    taskStatuses.has(value["status"]) &&
    typeof value["created_at"] === "string"
  );
}

function isErrorResponse(value: unknown): value is ErrorResponseWire {
  if (!isRecord(value) || !hasSchemaVersion(value) || !isRecord(value["error"])) {
    return false;
  }
  const error = value["error"];
  return (
    hasSchemaVersion(error) &&
    typeof error["code"] === "string" &&
    errorCodes.has(error["code"]) &&
    typeof error["message"] === "string" &&
    typeof error["retryable"] === "boolean" &&
    Array.isArray(error["details"]) &&
    error["details"].every(
      (detail) =>
        isRecord(detail) &&
        hasSchemaVersion(detail) &&
        typeof detail["reason"] === "string" &&
        (detail["field"] === undefined ||
          detail["field"] === null ||
          typeof detail["field"] === "string"),
    )
  );
}
