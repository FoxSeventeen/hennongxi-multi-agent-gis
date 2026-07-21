import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { MasterClient, ReadinessSnapshot } from "../api/client";
import { App } from "./App";

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

function createClient(getReadiness = vi.fn().mockResolvedValue(readySnapshot)): MasterClient {
  return {
    getReadiness,
    createTask: vi.fn(),
  };
}

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
    render(<App client={createClient(vi.fn().mockResolvedValue(blockedSnapshot))} />);

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
    render(<App client={createClient(getReadiness)} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取系统状态");
    await user.click(screen.getByRole("button", { name: "重新检查系统状态" }));

    expect(await screen.findByText("系统已就绪")).toBeVisible();
    expect(getReadiness).toHaveBeenCalledTimes(2);
  });
});
