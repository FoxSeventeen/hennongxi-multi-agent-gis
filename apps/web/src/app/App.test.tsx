import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AcceptedTask, MasterClient, ReadinessSnapshot } from "../api/client";
import type { TaskEvent, TaskSnapshot } from "../api/task-contract";
import { App } from "./App";

const taskId = "4f09fc09-6bd2-49fb-9636-7f4fb93baa44";
const correlationId = "f399c36a-6b76-4db5-a831-ebf6a170edf1";

const readySnapshot: ReadinessSnapshot = {
  ready: true,
  llmConfigured: true,
  dataConfigured: true,
  blockers: [],
  messages: ["系统已具备任务执行条件"],
  health: {
    state: "HEALTHY",
    checkedAt: "2024-08-12T08:00:00Z",
    services: [
      {
        service: "master",
        state: "HEALTHY",
        checkedAt: "2024-08-12T08:00:00Z",
        message: null,
      },
      {
        service: "analysis",
        state: "HEALTHY",
        checkedAt: "2024-08-12T08:00:00Z",
        message: null,
      },
    ],
  },
};

function createClient(overrides: Partial<MasterClient> = {}): MasterClient {
  return {
    getReadiness: vi.fn().mockResolvedValue(readySnapshot),
    createTask: vi.fn(),
    getTask: vi.fn(),
    retryTask: vi.fn(),
    streamTaskEvents: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("readiness application shell", () => {
  it("renders a map-first Chinese shell and resolved Agent readiness", async () => {
    render(<App client={createClient()} />);

    expect(screen.getByRole("heading", { level: 1, name: "神农溪生态监测指挥台" })).toBeVisible();
    expect(screen.getByRole("region", { name: "神农溪地图工作区" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("正在检查系统就绪状态");

    expect(await screen.findByText("系统已就绪")).toBeVisible();
    expect(screen.getByRole("button", { name: "创建监测任务" })).toBeEnabled();
    expect(screen.getByText("主控 Agent")).toBeVisible();
    expect(screen.getByText("分析 Agent")).toBeVisible();
    expect(screen.getAllByText("正常")).toHaveLength(2);
  });

  it("explains configuration blockers in Chinese", async () => {
    const blockedSnapshot: ReadinessSnapshot = {
      ...readySnapshot,
      ready: false,
      llmConfigured: false,
      blockers: ["LLM_NOT_CONFIGURED"],
      messages: [],
      health: { ...readySnapshot.health, state: "DEGRADED" },
    };
    render(
      <App client={createClient({ getReadiness: vi.fn().mockResolvedValue(blockedSnapshot) })} />,
    );

    expect(await screen.findByText("需要完成配置")).toBeVisible();
    expect(screen.getByText("尚未配置大模型访问凭据")).toBeVisible();
    expect(screen.getByText("待配置")).toBeVisible();
    expect(screen.getByRole("button", { name: "创建监测任务" })).toBeDisabled();
    expect(screen.getByText("系统就绪后才可创建任务")).toBeVisible();
  });

  it("offers an accessible retry after readiness loading fails", async () => {
    const getReadiness = vi
      .fn<MasterClient["getReadiness"]>()
      .mockRejectedValueOnce(new Error("network unavailable"))
      .mockResolvedValueOnce(readySnapshot);
    const user = userEvent.setup();
    render(<App client={createClient({ getReadiness })} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取系统状态");
    await user.click(screen.getByRole("button", { name: "重新检查系统状态" }));

    expect(await screen.findByText("系统已就绪")).toBeVisible();
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });

  it("reconstructs the Agent timeline from a task URL after refresh", async () => {
    window.history.replaceState(null, "", `/?task_id=${taskId}`);
    const completedSnapshot: TaskSnapshot = {
      taskId,
      query: "分析神农溪植被变化",
      status: "COMPLETED",
      progress: 100,
      currentAttempt: 1,
      correlationId,
      createdAt: "2024-08-12T08:30:00Z",
      updatedAt: "2024-08-12T08:30:05Z",
      plan: null,
      steps: [],
      lastError: null,
      analysis: null,
      quality: null,
      publication: null,
    };
    const terminalEvent: TaskEvent = {
      sequence: 8,
      taskId,
      stepId: "publish_results",
      attempt: 1,
      correlationId,
      agent: "publisher",
      status: "COMPLETED",
      progress: 100,
      message: "任务执行完成",
      elapsedMs: 5_000,
      occurredAt: "2024-08-12T08:30:05Z",
      error: null,
    };
    const getTask = vi.fn<MasterClient["getTask"]>().mockResolvedValue(completedSnapshot);
    const streamTaskEvents = vi.fn<MasterClient["streamTaskEvents"]>((_id, options) => {
      options.onEvent(terminalEvent);
      return Promise.resolve();
    });

    render(<App client={createClient({ getTask, streamTaskEvents })} />);

    expect(await screen.findByRole("heading", { level: 2, name: "Agent 执行时间线" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "监测成果与质量" })).toBeVisible();
    expect(screen.getByText(taskId)).toBeVisible();
    expect(await screen.findByText("任务已完成")).toBeVisible();
    expect(getTask).toHaveBeenCalledWith(taskId);
    expect(streamTaskEvents).toHaveBeenCalledWith(
      taskId,
      expect.objectContaining({ afterSequence: 0 }),
    );
  });

  it("binds an accepted task to the URL and opens its timeline", async () => {
    const acceptedTask: AcceptedTask = {
      taskId,
      status: "PENDING",
      createdAt: "2024-08-12T08:30:00Z",
    };
    const pendingSnapshot: TaskSnapshot = {
      taskId,
      query: "分析神农溪植被变化",
      status: "PENDING",
      progress: 0,
      currentAttempt: 1,
      correlationId,
      createdAt: acceptedTask.createdAt,
      updatedAt: acceptedTask.createdAt,
      plan: null,
      steps: [],
      lastError: null,
      analysis: null,
      quality: null,
      publication: null,
    };
    const createTask = vi.fn<MasterClient["createTask"]>().mockResolvedValue(acceptedTask);
    const getTask = vi.fn<MasterClient["getTask"]>().mockResolvedValue(pendingSnapshot);
    const streamTaskEvents = vi.fn<MasterClient["streamTaskEvents"]>(async (_id, options) => {
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
    const user = userEvent.setup();
    render(<App client={createClient({ createTask, getTask, streamTaskEvents })} />);

    await screen.findByText("系统已就绪");
    await user.type(screen.getByRole("textbox", { name: "生态监测任务描述" }), "分析神农溪植被变化");
    await user.click(screen.getByRole("button", { name: "创建监测任务" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Agent 执行时间线" })).toBeVisible();
    expect(new URL(window.location.href).searchParams.get("task_id")).toBe(taskId);
    expect(getTask).toHaveBeenCalledWith(taskId);
  });

  it("rejects an invalid task URL before it reaches the Master client", async () => {
    window.history.replaceState(null, "", "/?task_id=not-a-task");
    const getTask = vi.fn<MasterClient["getTask"]>();
    const user = userEvent.setup();
    render(<App client={createClient({ getTask })} />);

    expect(screen.getByRole("alert")).toHaveTextContent("任务编号格式无效");
    expect(getTask).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "返回新建任务" }));

    expect(new URL(window.location.href).searchParams.has("task_id")).toBe(false);
    expect(screen.queryByText("任务编号格式无效")).not.toBeInTheDocument();
  });
});
