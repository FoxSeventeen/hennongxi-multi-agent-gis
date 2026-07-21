import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { TaskEvent, TaskPlanStep, TaskSnapshot, TaskStep } from "../../api/task-contract";
import type { TaskTimelineState } from "./useTaskTimeline";
import { TaskTimelineView } from "./TaskTimeline";

const taskId = "4f09fc09-6bd2-49fb-9636-7f4fb93baa44";
const correlationId = "f399c36a-6b76-4db5-a831-ebf6a170edf1";

const planSteps: readonly TaskPlanStep[] = [
  {
    stepId: "prepare_data",
    kind: "prepare_data",
    agent: "data",
    order: 1,
    title: "准备神农溪流域数据",
    dependsOn: [],
  },
  {
    stepId: "analyze_ndvi_change",
    kind: "analyze_ndvi_change",
    agent: "analysis",
    order: 2,
    title: "计算双时相 NDVI 变化",
    dependsOn: ["prepare_data"],
  },
  {
    stepId: "evaluate_quality",
    kind: "evaluate_quality",
    agent: "quality",
    order: 3,
    title: "评价分析结果质量",
    dependsOn: ["analyze_ndvi_change"],
  },
  {
    stepId: "publish_results",
    kind: "publish_results",
    agent: "publisher",
    order: 4,
    title: "发布地图与监测报告",
    dependsOn: ["evaluate_quality"],
  },
];

const taskSteps: readonly TaskStep[] = [
  {
    stepId: "prepare_data",
    kind: "prepare_data",
    agent: "data",
    attempt: 1,
    status: "COMPLETED",
    progress: 100,
    startedAt: "2024-08-12T08:30:02Z",
    completedAt: "2024-08-12T08:30:03Z",
    elapsedMs: 1_200,
    error: null,
  },
  {
    stepId: "analyze_ndvi_change",
    kind: "analyze_ndvi_change",
    agent: "analysis",
    attempt: 1,
    status: "FAILED",
    progress: 35,
    startedAt: "2024-08-12T08:30:03Z",
    completedAt: "2024-08-12T08:30:05Z",
    elapsedMs: 2_450,
    error: {
      code: "ANALYSIS_FAILED",
      message: "分析栅格校验失败",
      retryable: true,
      details: [{ field: "after_nir", reason: "影像网格不一致" }],
    },
  },
];

const failedSnapshot: TaskSnapshot = {
  taskId,
  query: "分析神农溪植被变化",
  status: "FAILED",
  progress: 55,
  currentAttempt: 1,
  correlationId,
  createdAt: "2024-08-12T08:30:00Z",
  updatedAt: "2024-08-12T08:30:05Z",
  plan: {
    planId: "354da501-f92e-432d-8367-c845c16d6a07",
    source: "REAL_LLM",
    createdAt: "2024-08-12T08:30:01Z",
    modelCall: null,
    steps: planSteps,
  },
  steps: taskSteps,
  lastError: taskSteps[1]?.error ?? null,
};

const failedEvent: TaskEvent = {
  sequence: 7,
  taskId,
  stepId: "analyze_ndvi_change",
  attempt: 1,
  correlationId,
  agent: "analysis",
  status: "FAILED",
  progress: 55,
  message: "NDVI 变化分析失败",
  elapsedMs: 2_450,
  occurredAt: "2024-08-12T08:30:05Z",
  error: taskSteps[1]?.error ?? null,
};

function state(values: Partial<TaskTimelineState> = {}): TaskTimelineState {
  return {
    snapshot: failedSnapshot,
    events: [failedEvent],
    connection: "complete",
    problem: null,
    retry: vi.fn(),
    ...values,
  };
}

describe("Agent timeline presentation", () => {
  it("shows the shared task id and ordered Chinese Agent execution evidence", () => {
    render(<TaskTimelineView state={state()} />);

    expect(screen.getByRole("heading", { level: 2, name: "Agent 执行时间线" })).toBeVisible();
    expect(screen.getByText(taskId)).toBeVisible();
    expect(screen.getByText("第 1 次执行")).toBeVisible();
    expect(screen.getByText("真实大模型计划")).toBeVisible();
    expect(screen.getByText("任务失败")).toBeVisible();
    expect(screen.getByRole("progressbar", { name: "任务总进度" })).toHaveAttribute("value", "55");

    const stages = screen.getAllByTestId("timeline-stage");
    expect(stages).toHaveLength(5);
    expect(within(stageAt(stages, 0)).getByText("主控 Agent")).toBeVisible();
    expect(within(stageAt(stages, 1)).getByText("数据 Agent")).toBeVisible();
    expect(within(stageAt(stages, 2)).getByText("分析 Agent")).toBeVisible();
    expect(within(stageAt(stages, 3)).getByText("质量 Agent")).toBeVisible();
    expect(within(stageAt(stages, 4)).getByText("发布 Agent")).toBeVisible();
    expect(within(stageAt(stages, 1)).getByText("1.2 秒")).toBeVisible();
    expect(within(stageAt(stages, 2)).getByText("失败")).toBeVisible();
    expect(within(stageAt(stages, 2)).getByText("NDVI 变化分析失败")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("影像网格不一致");
  });

  it("announces polling recovery without hiding the last trusted snapshot", () => {
    render(
      <TaskTimelineView
        state={state({
          connection: "polling",
          problem: "实时连接已中断，正在通过任务查询恢复。",
        })}
      />,
    );

    expect(screen.getByText("轮询恢复")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("实时连接已中断");
    expect(screen.getByText(taskId)).toBeVisible();
  });

  it("offers a keyboard-accessible retry when the task cannot be loaded", async () => {
    const retry = vi.fn();
    const user = userEvent.setup();
    render(
      <TaskTimelineView
        state={state({
          snapshot: null,
          events: [],
          connection: "error",
          problem: "任务不存在或已被移除。",
          retry,
        })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("任务不存在或已被移除");
    await user.tab();
    expect(screen.getByRole("button", { name: "重新加载任务" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(retry).toHaveBeenCalledOnce();
  });
});

function stageAt(stages: readonly HTMLElement[], index: number): HTMLElement {
  const stage = stages[index];
  if (stage === undefined) {
    throw new Error(`缺少第 ${String(index + 1)} 个时间线阶段`);
  }
  return stage;
}
