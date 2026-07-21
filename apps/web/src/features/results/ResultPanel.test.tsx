import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MasterApiError, type AcceptedRetry, type MasterClient } from "../../api/client";
import { ResultPanel } from "./ResultPanel";
import { completedResultSnapshot, resultTaskId } from "./test-fixtures";

function client(retryTask: MasterClient["retryTask"] = vi.fn()): MasterClient {
  return {
    getReadiness: vi.fn(),
    createTask: vi.fn(),
    getTask: vi.fn(),
    retryTask,
    streamTaskEvents: vi.fn(),
  };
}

describe("results quality report 成果与质量面板", () => {
  it("向答辩查看者呈现完整统计、四项质量指标、真实大模型证据和 PDF", () => {
    render(
      <ResultPanel
        snapshot={completedResultSnapshot()}
        publisherBaseUrl="http://localhost:8004"
        client={client()}
        onRetryAccepted={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { level: 2, name: "监测成果与质量" })).toBeVisible();
    expect(screen.getByText("2019-08-19")).toBeVisible();
    expect(screen.getByText("2024-08-12")).toBeVisible();

    const statistics = screen.getByRole("group", { name: "NDVI 变化面积统计" });
    expect(within(statistics).getByText("增加")).toBeVisible();
    expect(within(statistics).getByText("128.40 公顷")).toBeVisible();
    expect(within(statistics).getByText("稳定")).toBeVisible();
    expect(within(statistics).getByText("702.60 公顷")).toBeVisible();
    expect(within(statistics).getByText("减少")).toBeVisible();
    expect(within(statistics).getByText("54.20 公顷")).toBeVisible();
    expect(within(statistics).getByText("有效面积")).toBeVisible();
    expect(within(statistics).getByText("885.20 公顷")).toBeVisible();

    const quality = screen.getByRole("group", { name: "四项质量指标" });
    expect(within(quality).getByText("流域覆盖率")).toBeVisible();
    expect(within(quality).getByText("98.20%")).toBeVisible();
    expect(within(quality).getByText("有效像元率")).toBeVisible();
    expect(within(quality).getByText("96.10%")).toBeVisible();
    expect(within(quality).getByText("输出完整性")).toBeVisible();
    expect(within(quality).getByText("完整")).toBeVisible();
    expect(within(quality).getByText("分析耗时")).toBeVisible();
    expect(within(quality).getByText("1.32 秒")).toBeVisible();
    expect(screen.getByText("质量结论：通过")).toBeVisible();

    expect(screen.getByText("真实大模型规划")).toBeVisible();
    expect(screen.getByText("claude-3-7-sonnet")).toBeVisible();
    expect(screen.getByText("640 毫秒 · 输入 128 / 输出 256 tokens")).toBeVisible();
    const report = screen.getByRole("link", { name: "下载中文 PDF 报告" });
    expect(report).toHaveAttribute(
      "href",
      `http://localhost:8004/api/v1/tasks/${resultTaskId}/artifacts/55555555-5555-4555-8555-555555555555/download`,
    );
  });

  it("失败任务不渲染完整、通过或报告下载入口", () => {
    render(
      <ResultPanel
        snapshot={completedResultSnapshot({ status: "FAILED" })}
        publisherBaseUrl="http://localhost:8004"
        client={client()}
        onRetryAccepted={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("本次执行未完成");
    expect(screen.queryByText("质量结论：通过")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "下载中文 PDF 报告" })).not.toBeInTheDocument();
  });

  it("显示责任步骤和结构化错误，并只提交一次受保护的失败重试", async () => {
    let acceptRetry: (retry: AcceptedRetry) => void = () => undefined;
    const pendingRetry = new Promise<AcceptedRetry>((resolve) => {
      acceptRetry = resolve;
    });
    const retryTask = vi.fn<MasterClient["retryTask"]>().mockReturnValue(pendingRetry);
    const onRetryAccepted = vi.fn();
    const user = userEvent.setup();
    render(
      <ResultPanel
        snapshot={completedResultSnapshot({ status: "FAILED" })}
        publisherBaseUrl="http://localhost:8004"
        client={client(retryTask)}
        onRetryAccepted={onRetryAccepted}
      />,
    );

    const failure = screen.getByRole("alert");
    expect(failure).toHaveTextContent("发布 Agent");
    expect(failure).toHaveTextContent("发布地图与监测报告");
    expect(failure).toHaveTextContent("发布服务暂时失败");
    expect(failure).toHaveTextContent("Publisher 暂时不可用");

    const retryButton = screen.getByRole("button", { name: "重试失败任务" });
    await user.click(retryButton);
    expect(retryButton).toBeDisabled();
    expect(retryTask).toHaveBeenCalledOnce();
    expect(retryTask).toHaveBeenCalledWith(resultTaskId);

    acceptRetry({
      taskId: resultTaskId,
      attempt: 2,
      status: "PENDING",
      acceptedAt: "2024-08-12T08:31:00Z",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "已接受第 2 次执行，正在重新连接",
    );
    expect(onRetryAccepted).toHaveBeenCalledOnce();
  });

  it("不可重试错误不显示操作，重试冲突则保留原失败证据", async () => {
    const nonRetryable = completedResultSnapshot({ status: "FAILED" });
    const locked = {
      ...nonRetryable,
      lastError:
        nonRetryable.lastError === null
          ? null
          : { ...nonRetryable.lastError, retryable: false },
      steps: nonRetryable.steps.map((step) => ({
        ...step,
        error: step.error === null ? null : { ...step.error, retryable: false },
      })),
    };
    const { rerender } = render(
      <ResultPanel
        snapshot={locked}
        publisherBaseUrl="http://localhost:8004"
        client={client()}
        onRetryAccepted={vi.fn()}
      />,
    );
    expect(screen.getByText("此错误不能从界面安全重试。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "重试失败任务" })).not.toBeInTheDocument();

    const retryTask = vi.fn<MasterClient["retryTask"]>().mockRejectedValue(
      new MasterApiError({
        code: "CONFLICT",
        message: "任务当前状态不能安全重试。",
        retryable: false,
      }),
    );
    rerender(
      <ResultPanel
        snapshot={completedResultSnapshot({ status: "FAILED" })}
        publisherBaseUrl="http://localhost:8004"
        client={client(retryTask)}
        onRetryAccepted={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "重试失败任务" }));
    expect(await screen.findByText("任务当前状态不能安全重试。")).toBeVisible();
    expect(screen.getByText("发布服务暂时失败")).toBeVisible();
  });
});
