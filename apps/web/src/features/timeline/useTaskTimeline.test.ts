import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MasterApiError, type MasterClient } from "../../api/client";
import type { TaskEvent, TaskSnapshot, TaskStatus } from "../../api/task-contract";
import { useTaskTimeline } from "./useTaskTimeline";

const taskId = "4f09fc09-6bd2-49fb-9636-7f4fb93baa44";
const correlationId = "f399c36a-6b76-4db5-a831-ebf6a170edf1";

function snapshot(status: TaskStatus, progress: number): TaskSnapshot {
  return {
    taskId,
    query: "分析神农溪植被变化",
    status,
    progress,
    currentAttempt: 1,
    correlationId,
    createdAt: "2024-08-12T08:30:00Z",
    updatedAt: "2024-08-12T08:30:03Z",
    plan: null,
    steps: [],
    lastError: null,
    analysis: null,
    quality: null,
    publication: null,
  };
}

function event(sequence: number, status: TaskStatus, progress: number): TaskEvent {
  return {
    sequence,
    taskId,
    stepId: status === "PLANNING" ? "planning" : "publish_results",
    attempt: 1,
    correlationId,
    agent: status === "PLANNING" ? "master" : "publisher",
    status,
    progress,
    message: status === "COMPLETED" ? "任务执行完成" : "正在生成执行计划",
    elapsedMs: sequence * 100,
    occurredAt: `2024-08-12T08:30:0${String(sequence)}Z`,
    error: null,
  };
}

function client(options: {
  readonly getTask: MasterClient["getTask"];
  readonly streamTaskEvents: MasterClient["streamTaskEvents"];
}): MasterClient {
  return {
    getReadiness: vi.fn(),
    createTask: vi.fn(),
    getTask: options.getTask,
    retryTask: vi.fn(),
    streamTaskEvents: options.streamTaskEvents,
  };
}

describe("timeline SSE and polling recovery", () => {
  it("keeps durable events ordered and unique before resolving the terminal snapshot", async () => {
    const planning = event(1, "PLANNING", 5);
    const completed = event(2, "COMPLETED", 100);
    const getTask = vi
      .fn<MasterClient["getTask"]>()
      .mockResolvedValueOnce(snapshot("PLANNING", 5))
      .mockResolvedValueOnce(snapshot("COMPLETED", 100));
    const streamTaskEvents = vi.fn<MasterClient["streamTaskEvents"]>((_id, options) => {
      options.onEvent(planning);
      options.onEvent(planning);
      options.onEvent(completed);
      return Promise.resolve();
    });
    const masterClient = client({ getTask, streamTaskEvents });
    const retryDelaysMs = [0] as const;

    const { result } = renderHook(() =>
      useTaskTimeline(masterClient, taskId, { retryDelaysMs }),
    );

    await waitFor(() => {
      expect(result.current.connection).toBe("complete");
    });
    expect(result.current.snapshot).toMatchObject({ status: "COMPLETED", progress: 100 });
    expect(result.current.events.map((item) => item.sequence)).toEqual([1, 2]);
    expect(streamTaskEvents).toHaveBeenCalledTimes(1);
    expect(getTask).toHaveBeenCalledTimes(2);
  });

  it("falls back to polling and resumes streaming after the last unique sequence", async () => {
    const planning = event(3, "PLANNING", 5);
    const completed = event(4, "COMPLETED", 100);
    let resolvePoll: (value: TaskSnapshot) => void = () => undefined;
    const pendingPoll = new Promise<TaskSnapshot>((resolve) => {
      resolvePoll = resolve;
    });
    const getTask = vi
      .fn<MasterClient["getTask"]>()
      .mockResolvedValueOnce(snapshot("PLANNING", 5))
      .mockReturnValueOnce(pendingPoll)
      .mockResolvedValueOnce(snapshot("COMPLETED", 100));
    const streamTaskEvents = vi
      .fn<MasterClient["streamTaskEvents"]>()
      .mockImplementationOnce((_id, options) => {
        options.onEvent(planning);
        return Promise.reject(
          new MasterApiError({
            code: "DEPENDENCY_UNAVAILABLE",
            message: "SSE 暂时断开",
            retryable: true,
          }),
        );
      })
      .mockImplementationOnce((_id, options) => {
        options.onEvent(planning);
        options.onEvent(completed);
        return Promise.resolve();
      });
    const masterClient = client({ getTask, streamTaskEvents });
    const retryDelaysMs = [0] as const;

    const { result } = renderHook(() =>
      useTaskTimeline(masterClient, taskId, { retryDelaysMs }),
    );

    await waitFor(() => {
      expect(result.current.connection).toBe("polling");
    });
    expect(result.current.events.map((item) => item.sequence)).toEqual([3]);

    await act(async () => {
      resolvePoll(snapshot("PLANNING", 5));
      await pendingPoll;
    });

    await waitFor(() => {
      expect(result.current.connection).toBe("complete");
    });
    expect(streamTaskEvents).toHaveBeenNthCalledWith(
      2,
      taskId,
      expect.objectContaining({ afterSequence: 3 }),
    );
    expect(result.current.events.map((item) => item.sequence)).toEqual([3, 4]);
  });

  it("reconstructs an already completed task from polling when streaming is unavailable", async () => {
    const getTask = vi.fn<MasterClient["getTask"]>().mockResolvedValue(snapshot("COMPLETED", 100));
    const streamTaskEvents = vi.fn<MasterClient["streamTaskEvents"]>().mockRejectedValue(
      new MasterApiError({
        code: "DEPENDENCY_UNAVAILABLE",
        message: "SSE 暂时不可用",
        retryable: true,
      }),
    );
    const masterClient = client({ getTask, streamTaskEvents });
    const retryDelaysMs = [0] as const;

    const { result } = renderHook(() =>
      useTaskTimeline(masterClient, taskId, { retryDelaysMs }),
    );

    await waitFor(() => {
      expect(result.current.connection).toBe("complete");
    });
    expect(result.current.snapshot).toMatchObject({ taskId, status: "COMPLETED" });
    expect(getTask).toHaveBeenCalledTimes(2);
  });

  it("stops without opening a stream when the task URL cannot resolve", async () => {
    const getTask = vi.fn<MasterClient["getTask"]>().mockRejectedValue(
      new MasterApiError({
        code: "TASK_NOT_FOUND",
        message: "任务不存在或已被移除。",
        retryable: false,
      }),
    );
    const streamTaskEvents = vi.fn<MasterClient["streamTaskEvents"]>();
    const masterClient = client({ getTask, streamTaskEvents });
    const retryDelaysMs = [0] as const;

    const { result } = renderHook(() =>
      useTaskTimeline(masterClient, taskId, { retryDelaysMs }),
    );

    await waitFor(() => {
      expect(result.current.connection).toBe("error");
    });
    expect(result.current.problem).toBe("任务不存在或已被移除。");
    expect(streamTaskEvents).not.toHaveBeenCalled();
  });

  it("refreshes the plan when streaming advances beyond planning", async () => {
    const pendingSnapshot = snapshot("PENDING", 0);
    const plannedSnapshot: TaskSnapshot = {
      ...snapshot("DATA_PREPARING", 10),
      plan: {
        planId: "354da501-f92e-432d-8367-c845c16d6a07",
        source: "REAL_LLM",
        createdAt: "2024-08-12T08:30:01Z",
        modelCall: null,
        steps: [
          {
            stepId: "prepare_data",
            kind: "prepare_data",
            agent: "data",
            order: 1,
            title: "准备神农溪流域数据",
            dependsOn: [],
          },
        ],
      },
    };
    const getTask = vi
      .fn<MasterClient["getTask"]>()
      .mockResolvedValueOnce(pendingSnapshot)
      .mockResolvedValueOnce(plannedSnapshot);
    const streamTaskEvents = vi.fn<MasterClient["streamTaskEvents"]>(async (_id, options) => {
      options.onEvent({
        ...event(1, "DATA_PREPARING", 10),
        stepId: "prepare_data",
        agent: "data",
      });
      await new Promise<void>((resolve) => {
        options.signal.addEventListener(
          "abort",
          () => {
            resolve();
          },
          { once: true },
        );
      });
    });
    const masterClient = client({ getTask, streamTaskEvents });
    const retryDelaysMs = [0] as const;

    const { result, unmount } = renderHook(() =>
      useTaskTimeline(masterClient, taskId, { retryDelaysMs }),
    );

    await waitFor(() => {
      expect(result.current.snapshot?.plan?.steps).toHaveLength(1);
    });
    expect(getTask).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("aborts the active stream when the timeline unmounts", async () => {
    let streamSignal: AbortSignal | null = null;
    const getTask = vi.fn<MasterClient["getTask"]>().mockResolvedValue(snapshot("ANALYZING", 40));
    const streamTaskEvents = vi.fn<MasterClient["streamTaskEvents"]>(async (_id, options) => {
      streamSignal = options.signal;
      await new Promise<void>((resolve) => {
        options.signal.addEventListener(
          "abort",
          () => {
            resolve();
          },
          { once: true },
        );
      });
    });
    const masterClient = client({ getTask, streamTaskEvents });
    const retryDelaysMs = [0] as const;

    const { unmount } = renderHook(() =>
      useTaskTimeline(masterClient, taskId, { retryDelaysMs }),
    );
    await waitFor(() => {
      expect(streamTaskEvents).toHaveBeenCalledOnce();
    });

    unmount();

    expect(streamSignal).not.toBeNull();
    expect(streamSignal).toHaveProperty("aborted", true);
  });
});
