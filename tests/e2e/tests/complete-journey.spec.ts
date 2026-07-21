import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures.js";

test("确定性 Compose 主旅程完成地图、指标、报告与刷新恢复", async ({
  page,
  browserMessages,
}) => {
  await setStudyAreaMode(page, "degraded");
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "系统已就绪" })).toBeVisible();

  await page.getByLabel("生态监测任务描述").fill("分析神农溪 2019 至 2024 年植被变化");
  await page.getByRole("button", { name: "创建监测任务" }).click();

  await expect(page.getByRole("status").filter({ hasText: "任务已创建" })).toBeVisible();
  const taskId = new URL(page.url()).searchParams.get("task_id");
  expect(taskId).toMatch(/^[0-9a-f-]{36}$/);
  await expect(page.getByText(taskId ?? "missing task id", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/在线位置校验已降级/)).toBeVisible();

  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("timeline-stage")).toHaveCount(5);
  await expect(page.getByTestId("timeline-stage").getByText("已完成", { exact: true })).toHaveCount(
    5,
  );

  await expect(page.getByRole("button", { name: "前期 NDVI" })).toBeVisible();
  await expect(page.getByRole("button", { name: "后期 NDVI" })).toBeVisible();
  await expect(page.getByRole("button", { name: "NDVI 差值" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByText("流域边界始终显示")).toBeVisible();
  await expect(page.getByRole("group", { name: /神农溪完整流域边界与NDVI 差值图层/ })).toBeVisible();

  await expect(page.getByRole("group", { name: "NDVI 变化面积统计" })).toContainText(
    "增加0.04 公顷",
  );
  await expect(page.getByRole("group", { name: "NDVI 变化面积统计" })).toContainText(
    "稳定0.08 公顷",
  );
  await expect(page.getByRole("group", { name: "NDVI 变化面积统计" })).toContainText(
    "减少0.04 公顷",
  );
  await expect(page.getByRole("group", { name: "NDVI 变化面积统计" })).toContainText(
    "有效面积0.16 公顷",
  );
  await expect(page.getByRole("group", { name: "四项质量指标" })).toContainText("100.00%");
  await expect(page.getByText("质量结论：通过", { exact: true })).toBeVisible();
  await expect(page.getByText("真实大模型规划", { exact: true })).toBeVisible();

  const reportLink = page.getByRole("link", { name: "下载中文 PDF 报告" });
  await expect(reportLink).toHaveAttribute(
    "href",
    new RegExp(`/api/v1/tasks/${taskId ?? "missing"}/artifacts/.+/download$`),
  );
  const reportResponse = await page.request.get(await reportLink.getAttribute("href") ?? "");
  expect(reportResponse.status()).toBe(200);
  expect(reportResponse.headers()["content-type"]).toContain("application/pdf");

  const taskUrl = page.url();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(taskUrl);
  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible();
  await expect(page.getByText(taskId ?? "missing task id", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("group", { name: "NDVI 变化面积统计" })).toContainText(
    "有效面积0.16 公顷",
  );
  expect(browserMessages).toEqual([]);
});

test("确定性 Compose 主旅程显示在线位置校验通过且仍完成同一成果链", async ({
  page,
  browserMessages,
}) => {
  await setStudyAreaMode(page, "verified");
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "系统已就绪" })).toBeVisible();

  await page.getByLabel("生态监测任务描述").fill("分析巴东县神农溪 2019 至 2024 年植被变化");
  await page.getByRole("button", { name: "创建监测任务" }).click();

  await expect(page.getByRole("status").filter({ hasText: "任务已创建" })).toBeVisible();
  const taskId = new URL(page.url()).searchParams.get("task_id");
  expect(taskId).toMatch(/^[0-9a-f-]{36}$/);
  await expect(page.getByText(/在线位置校验通过/)).toBeVisible();
  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("timeline-stage")).toHaveCount(5);
  await expect(page.getByRole("group", { name: "NDVI 变化面积统计" })).toContainText(
    "有效面积0.16 公顷",
  );
  await expect(page.getByText("质量结论：通过", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "下载中文 PDF 报告" })).toHaveAttribute(
    "href",
    new RegExp(`/api/v1/tasks/${taskId ?? "missing"}/artifacts/.+/download$`),
  );
  expect(browserMessages).toEqual([]);
});

async function setStudyAreaMode(page: Page, mode: "verified" | "degraded"): Promise<void> {
  const response = await page.request.put(
    "http://127.0.0.1:8000/internal/e2e/v1/study-area-mode",
    {
      headers: { "X-E2E-Control": "deterministic-e2e-control" },
      data: { mode },
    },
  );
  expect(response.status()).toBe(204);
}
