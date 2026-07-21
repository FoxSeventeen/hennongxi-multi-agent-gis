import { useCallback, useEffect, useState } from "react";

import { MasterApiError, type MasterClient } from "../../api/client";
import type { TaskEvent, TaskSnapshot, TaskStatus } from "../../api/task-contract";

const defaultRetryDelaysMs = [1_000, 2_000, 4_000, 8_000] as const;
const terminalStatuses = new Set<TaskStatus>(["COMPLETED", "FAILED"]);
const postPlanningStatuses = new Set<TaskStatus>([
  "DATA_PREPARING",
  "ANALYZING",
  "QUALITY_CHECKING",
  "PUBLISHING",
]);

export type TimelineConnection =
  | "loading"
  | "streaming"
  | "polling"
  | "reconnecting"
  | "complete"
  | "error";

export interface TaskTimelineState {
  readonly snapshot: TaskSnapshot | null;
  readonly events: readonly TaskEvent[];
  readonly connection: TimelineConnection;
  readonly problem: string | null;
  readonly retry: () => void;
}

interface TimelineOptions {
  readonly retryDelaysMs?: readonly number[];
}

export function useTaskTimeline(
  client: MasterClient,
  taskId: string,
  options: TimelineOptions = {},
): TaskTimelineState {
  const [snapshot, setSnapshot] = useState<TaskSnapshot | null>(null);
  const [events, setEvents] = useState<readonly TaskEvent[]>([]);
  const [connection, setConnection] = useState<TimelineConnection>("loading");
  const [problem, setProblem] = useState<string | null>(null);
  const [retryIndex, setRetryIndex] = useState(0);
  const retryDelaysMs = normalizeRetryDelays(options.retryDelaysMs);
  const retryKey = retryDelaysMs.join(",");

  useEffect(() => {
    const controller = new AbortController();
    const eventsBySequence = new Map<number, TaskEvent>();
    let lastSequence = 0;
    let fatalFailure = false;
    let latestSnapshot: TaskSnapshot | null = null;
    let planRefreshPending = false;

    setSnapshot(null);
    setEvents([]);
    setConnection("loading");
    setProblem(null);

    function acceptEvent(event: TaskEvent): void {
      if (isStopped(controller.signal) || eventsBySequence.has(event.sequence)) {
        return;
      }
      eventsBySequence.set(event.sequence, event);
      lastSequence = Math.max(lastSequence, event.sequence);
      setEvents([...eventsBySequence.values()].sort((left, right) => left.sequence - right.sequence));
      if (latestSnapshot !== null) {
        latestSnapshot = {
          ...latestSnapshot,
          status: event.status,
          progress: event.progress,
          currentAttempt: Math.max(latestSnapshot.currentAttempt, event.attempt),
          updatedAt: event.occurredAt,
          lastError: event.error ?? latestSnapshot.lastError,
        };
        setSnapshot(latestSnapshot);
        if (
          latestSnapshot.plan === null &&
          postPlanningStatuses.has(event.status) &&
          !planRefreshPending
        ) {
          planRefreshPending = true;
          void refreshSnapshot().finally(() => {
            planRefreshPending = false;
          });
        }
      }
    }

    async function refreshSnapshot(): Promise<TaskSnapshot | null> {
      try {
        const nextSnapshot = await client.getTask(taskId);
        latestSnapshot = nextSnapshot;
        if (!isStopped(controller.signal)) {
          setSnapshot(nextSnapshot);
          setProblem(null);
        }
        fatalFailure = false;
        return nextSnapshot;
      } catch (reason: unknown) {
        fatalFailure = isFatal(reason);
        if (!isStopped(controller.signal)) {
          setProblem(toSafeProblem(reason));
        }
        if (fatalFailure) {
          setConnection("error");
        }
        return null;
      }
    }

    async function run(): Promise<void> {
      const initialSnapshot = await refreshSnapshot();
      if (isStopped(controller.signal)) {
        return;
      }
      if (initialSnapshot === null && fatalFailure) {
        return;
      }

      let recoveryAttempt = 0;
      for (;;) {
        setConnection(recoveryAttempt === 0 ? "streaming" : "reconnecting");
        try {
          await client.streamTaskEvents(taskId, {
            afterSequence: lastSequence,
            signal: controller.signal,
            onEvent(event) {
              acceptEvent(event);
            },
          });
        } catch (reason: unknown) {
          if (isStopped(controller.signal)) {
            return;
          }
          if (isFatal(reason)) {
            setProblem(toSafeProblem(reason));
            setConnection("error");
            return;
          }
          setProblem("实时连接已中断，正在通过任务查询恢复。");
        }

        if (isStopped(controller.signal)) {
          return;
        }
        const latestEvent = eventsBySequence.get(lastSequence);
        if (latestEvent !== undefined && isTerminal(latestEvent.status)) {
          const finalSnapshot = await refreshSnapshot();
          if (!isStopped(controller.signal)) {
            if (finalSnapshot === null) {
              setProblem("已收到任务终态，最新步骤详情暂时无法读取。");
            }
            setConnection("complete");
          }
          return;
        }

        setConnection("polling");
        const delayMs = retryDelaysMs[Math.min(recoveryAttempt, retryDelaysMs.length - 1)] ?? 8_000;
        try {
          await waitForDelay(delayMs, controller.signal);
        } catch {
          return;
        }
        if (isStopped(controller.signal)) {
          return;
        }

        const polledSnapshot = await refreshSnapshot();
        if (isStopped(controller.signal)) {
          return;
        }
        if (polledSnapshot !== null && isTerminal(polledSnapshot.status)) {
          setConnection("complete");
          return;
        }
        if (polledSnapshot === null && fatalFailure) {
          return;
        }
        recoveryAttempt += 1;
      }
    }

    void run();
    return () => {
      controller.abort();
    };
  }, [client, retryIndex, retryKey, taskId]);

  const retry = useCallback(() => {
    setRetryIndex((current) => current + 1);
  }, []);

  return { snapshot, events, connection, problem, retry };
}

function normalizeRetryDelays(values: readonly number[] | undefined): readonly number[] {
  if (
    values === undefined ||
    values.length === 0 ||
    values.some((value) => !Number.isSafeInteger(value) || value < 0 || value > 30_000)
  ) {
    return defaultRetryDelaysMs;
  }
  return values;
}

function isTerminal(status: TaskStatus): boolean {
  return terminalStatuses.has(status);
}

function isFatal(reason: unknown): boolean {
  return reason instanceof MasterApiError && !reason.retryable;
}

function isStopped(signal: AbortSignal): boolean {
  return signal.aborted;
}

function toSafeProblem(reason: unknown): string {
  if (reason instanceof MasterApiError) {
    return reason.message;
  }
  return "暂时无法读取任务进度，请稍后重试。";
}

function waitForDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) {
    return Promise.reject(new Error("timeline stopped"));
  }
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", handleAbort);
      resolve();
    }, milliseconds);
    function handleAbort(): void {
      window.clearTimeout(timer);
      reject(new Error("timeline stopped"));
    }
    signal.addEventListener("abort", handleAbort, { once: true });
  });
}
