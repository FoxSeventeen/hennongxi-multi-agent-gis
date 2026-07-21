import type {
  AgentName,
  StepStatus,
  TaskEvent,
  TaskSnapshot,
  TimelineError,
} from "../../api/task-contract";

export interface TimelineStage {
  readonly key: string;
  readonly title: string;
  readonly agent: AgentName;
  readonly status: StepStatus;
  readonly progress: number;
  readonly message: string;
  readonly elapsedMs: number | null;
  readonly occurredAt: string | null;
  readonly error: TimelineError | null;
}

const activeOrderByStatus = {
  DATA_PREPARING: 1,
  ANALYZING: 2,
  QUALITY_CHECKING: 3,
  PUBLISHING: 4,
} as const;

export function buildTimelineStages(
  snapshot: TaskSnapshot,
  events: readonly TaskEvent[],
): readonly TimelineStage[] {
  const currentEvents = events.filter((event) => event.attempt === snapshot.currentAttempt);
  const latestMasterEvent = findLatestEvent(currentEvents, (event) => event.agent === "master");
  const failedEvent = findLatestEvent(currentEvents, (event) => event.status === "FAILED");
  const failedStepId =
    failedEvent?.stepId ?? snapshot.steps.find((step) => step.status === "FAILED")?.stepId ?? null;

  const masterFailed = snapshot.status === "FAILED" && (failedStepId === "planning" || failedEvent?.agent === "master");
  const masterStatus: StepStatus = masterFailed
    ? "FAILED"
    : snapshot.status === "PENDING"
      ? "PENDING"
      : snapshot.status === "PLANNING"
        ? "RUNNING"
        : "COMPLETED";
  const master: TimelineStage = {
    key: "planning",
    title: "生成受约束执行计划",
    agent: "master",
    status: masterStatus,
    progress: masterStatus === "COMPLETED" ? 100 : masterStatus === "PENDING" ? 0 : snapshot.progress,
    message: latestMasterEvent?.message ?? getMasterMessage(masterStatus),
    elapsedMs: latestMasterEvent?.elapsedMs ?? null,
    occurredAt: latestMasterEvent?.occurredAt ?? snapshot.createdAt,
    error: masterFailed ? (latestMasterEvent?.error ?? snapshot.lastError) : null,
  };

  if (snapshot.plan === null) {
    return [master];
  }

  const failedOrder = snapshot.plan.steps.find((step) => step.stepId === failedStepId)?.order ?? null;
  const planStages = [...snapshot.plan.steps]
    .sort((left, right) => left.order - right.order)
    .map((planStep): TimelineStage => {
      const taskStep = snapshot.steps.find(
        (step) => step.stepId === planStep.stepId && step.attempt === snapshot.currentAttempt,
      );
      const latestEvent = findLatestEvent(
        currentEvents,
        (event) => event.stepId === planStep.stepId,
      );
      const status = deriveStepStatus(snapshot, planStep.order, planStep.stepId, failedOrder, taskStep?.status);
      const error =
        status === "FAILED"
          ? (taskStep?.error ?? latestEvent?.error ?? snapshot.lastError)
          : taskStep?.error ?? null;
      return {
        key: planStep.stepId,
        title: planStep.title,
        agent: planStep.agent,
        status,
        progress: getStageProgress(status, taskStep?.progress),
        message: latestEvent?.message ?? getStepMessage(status),
        elapsedMs: taskStep?.elapsedMs ?? latestEvent?.elapsedMs ?? null,
        occurredAt:
          taskStep?.completedAt ?? latestEvent?.occurredAt ?? taskStep?.startedAt ?? null,
        error,
      };
    });
  return [master, ...planStages];
}

function deriveStepStatus(
  snapshot: TaskSnapshot,
  order: number,
  stepId: string,
  failedOrder: number | null,
  recordedStatus: StepStatus | undefined,
): StepStatus {
  if (recordedStatus === "FAILED" || recordedStatus === "SKIPPED") {
    return recordedStatus;
  }
  if (snapshot.status === "COMPLETED") {
    return "COMPLETED";
  }
  if (snapshot.status === "FAILED") {
    if (snapshot.steps.some((step) => step.stepId === stepId && step.status === "FAILED")) {
      return "FAILED";
    }
    if (failedOrder === null) {
      return recordedStatus ?? "PENDING";
    }
    if (order === failedOrder) {
      return "FAILED";
    }
    return order < failedOrder ? "COMPLETED" : "PENDING";
  }
  if (snapshot.status === "PENDING" || snapshot.status === "PLANNING") {
    return recordedStatus ?? "PENDING";
  }
  const activeOrder = activeOrderByStatus[snapshot.status];
  if (order < activeOrder) {
    return "COMPLETED";
  }
  if (order === activeOrder) {
    return "RUNNING";
  }
  return "PENDING";
}

function getStageProgress(status: StepStatus, recordedProgress: number | undefined): number {
  if (status === "COMPLETED" || status === "SKIPPED") {
    return 100;
  }
  if (status === "PENDING") {
    return 0;
  }
  return recordedProgress ?? 0;
}

function findLatestEvent(
  events: readonly TaskEvent[],
  predicate: (event: TaskEvent) => boolean,
): TaskEvent | undefined {
  return events.reduce<TaskEvent | undefined>(
    (latest, event) =>
      predicate(event) && (latest === undefined || event.sequence > latest.sequence) ? event : latest,
    undefined,
  );
}

function getMasterMessage(status: StepStatus): string {
  if (status === "PENDING") {
    return "等待 Master 接收任务";
  }
  if (status === "RUNNING") {
    return "正在生成并校验执行计划";
  }
  if (status === "FAILED") {
    return "执行计划生成失败";
  }
  return "执行计划已通过约束校验";
}

function getStepMessage(status: StepStatus): string {
  if (status === "PENDING") {
    return "等待前序 Agent 完成";
  }
  if (status === "RUNNING") {
    return "Agent 正在执行此步骤";
  }
  if (status === "FAILED") {
    return "此步骤执行失败";
  }
  if (status === "SKIPPED") {
    return "已从安全检查点复用结果";
  }
  return "此步骤已完成";
}
