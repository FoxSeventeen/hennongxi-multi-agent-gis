import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  MasterApiError,
  type AcceptedTask,
  type MasterClient,
  type ReadinessSnapshot,
} from "../api/client";
import { TaskComposer } from "./TaskComposer";

const acceptedTask: AcceptedTask = {
  taskId: "4f09fc09-6bd2-49fb-9636-7f4fb93baa44",
  status: "PENDING",
  createdAt: "2024-08-12T08:30:00Z",
};

const unusedReadiness: ReadinessSnapshot = {
  ready: true,
  llmConfigured: true,
  dataConfigured: true,
  blockers: [],
  messages: [],
  health: {
    state: "HEALTHY",
    checkedAt: "2024-08-12T08:00:00Z",
    services: [],
  },
};

function createClient(createTask: MasterClient["createTask"]): MasterClient {
  return {
    createTask,
    getReadiness: vi.fn().mockResolvedValue(unusedReadiness),
    getTask: vi.fn(),
    retryTask: vi.fn(),
    streamTaskEvents: vi.fn(),
  };
}

describe("task submission composer", () => {
  it("submits one normalized Chinese request while repeated clicks are locked", async () => {
    let resolveTask: (task: AcceptedTask) => void = () => undefined;
    const pendingTask = new Promise<AcceptedTask>((resolve) => {
      resolveTask = resolve;
    });
    const createTask = vi.fn<MasterClient["createTask"]>().mockReturnValue(pendingTask);
    const onAccepted = vi.fn();
    const user = userEvent.setup();
    render(<TaskComposer client={createClient(createTask)} canSubmit onAccepted={onAccepted} />);

    await user.type(screen.getByRole("textbox", { name: "生态监测任务描述" }), "  分析神农溪植被变化  ");
    await user.dblClick(screen.getByRole("button", { name: "创建监测任务" }));

    expect(createTask).toHaveBeenCalledTimes(1);
    expect(createTask).toHaveBeenCalledWith("分析神农溪植被变化");
    expect(screen.getByRole("button", { name: "正在创建任务" })).toBeDisabled();

    resolveTask(acceptedTask);

    expect(await screen.findByRole("status")).toHaveTextContent("任务已创建");
    expect(screen.getByText(acceptedTask.taskId)).toBeVisible();
    expect(onAccepted).toHaveBeenCalledOnce();
    expect(onAccepted).toHaveBeenCalledWith(acceptedTask);
  });

  it("renders safe structured errors and allows a retry", async () => {
    const createTask = vi
      .fn<MasterClient["createTask"]>()
      .mockRejectedValueOnce(
        new MasterApiError({
          code: "DEPENDENCY_UNAVAILABLE",
          message: "分析服务暂不可用",
          retryable: true,
          details: [{ field: "analysis", reason: "健康检查失败" }],
        }),
      )
      .mockResolvedValueOnce(acceptedTask);
    const user = userEvent.setup();
    render(<TaskComposer client={createClient(createTask)} canSubmit onAccepted={vi.fn()} />);

    await user.type(screen.getByRole("textbox", { name: "生态监测任务描述" }), "分析神农溪植被变化");
    await user.click(screen.getByRole("button", { name: "创建监测任务" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("分析服务暂不可用");
    expect(screen.getByRole("alert")).toHaveTextContent("健康检查失败");
    expect(screen.getByRole("button", { name: "重试创建任务" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "重试创建任务" }));
    expect(await screen.findByRole("status")).toHaveTextContent("任务已创建");
    expect(createTask).toHaveBeenCalledTimes(2);
  });

  it("keeps submission disabled until readiness permits it", () => {
    const createTask = vi.fn<MasterClient["createTask"]>();
    render(
      <TaskComposer
        client={createClient(createTask)}
        canSubmit={false}
        disabledReason="系统就绪后才可创建任务"
        onAccepted={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "创建监测任务" })).toBeDisabled();
    expect(screen.getByText("系统就绪后才可创建任务")).toBeVisible();
    expect(createTask).not.toHaveBeenCalled();
  });

  it("supports the complete keyboard focus and submit path", async () => {
    const createTask = vi.fn<MasterClient["createTask"]>().mockResolvedValue(acceptedTask);
    const user = userEvent.setup();
    render(<TaskComposer client={createClient(createTask)} canSubmit onAccepted={vi.fn()} />);

    await user.tab();
    const taskInput = screen.getByRole("textbox", { name: "生态监测任务描述" });
    expect(taskInput).toHaveFocus();
    await user.type(taskInput, "分析神农溪植被变化");
    await user.tab();
    expect(screen.getByRole("button", { name: "创建监测任务" })).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(await screen.findByRole("status")).toHaveTextContent("任务已创建");
    expect(createTask).toHaveBeenCalledOnce();
  });

  it("announces blank input validation without calling Master", async () => {
    const createTask = vi.fn<MasterClient["createTask"]>();
    const user = userEvent.setup();
    render(<TaskComposer client={createClient(createTask)} canSubmit onAccepted={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "创建监测任务" }));

    expect(screen.getByRole("alert")).toHaveTextContent("请输入需要分析的生态监测任务");
    expect(screen.getByRole("textbox", { name: "生态监测任务描述" })).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(createTask).not.toHaveBeenCalled();
  });
});
