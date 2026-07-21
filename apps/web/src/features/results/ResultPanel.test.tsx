import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResultPanel } from "./ResultPanel";
import { completedResultSnapshot, resultTaskId } from "./test-fixtures";

describe("results quality report 成果与质量面板", () => {
  it("向答辩查看者呈现完整统计、四项质量指标、真实大模型证据和 PDF", () => {
    render(
      <ResultPanel
        snapshot={completedResultSnapshot()}
        publisherBaseUrl="http://localhost:8004"
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
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("本次执行未完成");
    expect(screen.queryByText("质量结论：通过")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "下载中文 PDF 报告" })).not.toBeInTheDocument();
  });
});
