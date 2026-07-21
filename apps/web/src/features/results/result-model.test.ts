import { describe, expect, it } from "vitest";

import { buildResultPresentation } from "./result-model";
import { completedResultSnapshot, resultTaskId } from "./test-fixtures";

describe("results 成果展示模型", () => {
  it("只为完整通过的当前尝试生成日期、统计、质量、大模型与报告证据", () => {
    const result = buildResultPresentation(
      completedResultSnapshot(),
      "http://localhost:8004/internal/path",
    );

    expect(result.status).toBe("ready");
    if (result.status !== "ready") {
      return;
    }
    expect(result.presentation).toMatchObject({
      taskId: resultTaskId,
      attempt: 1,
      beforeDate: "2019-08-19",
      afterDate: "2024-08-12",
      units: "公顷",
      statistics: {
        increaseHectares: 128.4,
        stableHectares: 702.6,
        decreaseHectares: 54.2,
        validHectares: 885.2,
      },
      quality: {
        coverageRatio: 0.982,
        validPixelRatio: 0.961,
        outputComplete: true,
        elapsedMs: 1320,
        conclusion: "PASS",
      },
      planning: {
        source: "REAL_LLM",
        label: "真实大模型规划",
        model: "claude-3-7-sonnet",
        status: "SUCCEEDED",
      },
      report: {
        url: `http://localhost:8004/api/v1/tasks/${resultTaskId}/artifacts/55555555-5555-4555-8555-555555555555/download`,
        byteSize: 24_576,
        checksumSha256: "b".repeat(64),
      },
    });
  });

  it("明确标注内置恢复计划，且不把它作为真实大模型证据", () => {
    const result = buildResultPresentation(
      completedResultSnapshot({ source: "BUILTIN_RECOVERY" }),
      "http://localhost:8004",
    );

    expect(result.status).toBe("ready");
    if (result.status !== "ready") {
      return;
    }
    expect(result.presentation.planning).toMatchObject({
      source: "BUILTIN_RECOVERY",
      label: "内置恢复计划",
      isAcceptanceEvidence: false,
    });
  });

  it("失败任务即使携带部分旧结果也不能显示为完整成果", () => {
    const result = buildResultPresentation(
      completedResultSnapshot({ status: "FAILED" }),
      "http://localhost:8004",
    );

    expect(result).toEqual({
      status: "incomplete",
      message: "本次执行未完成，不展示完整成果。",
    });
  });

  it("拒绝带凭据或非 HTTP 协议的 Publisher 地址", () => {
    expect(
      buildResultPresentation(completedResultSnapshot(), "https://user:secret@example.test"),
    ).toEqual({ status: "unavailable", message: "报告下载地址配置无效。" });
    expect(buildResultPresentation(completedResultSnapshot(), "javascript:alert(1)"))
      .toEqual({ status: "unavailable", message: "报告下载地址配置无效。" });
  });
});
