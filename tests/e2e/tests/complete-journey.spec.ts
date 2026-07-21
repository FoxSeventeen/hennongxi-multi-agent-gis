import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures.js";

test("确定性 Compose 主旅程完成地图、指标、报告与刷新恢复", async ({
  page,
  browserMessages,
}) => {
  await armPublisherFailures(page, 0);
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
  await armPublisherFailures(page, 0);
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

test("Publisher 强制失败后界面保留证据并从安全检查点重试完成", async ({
  page,
  browserMessages,
}) => {
  await armPublisherFailures(page, 1);
  await setStudyAreaMode(page, "degraded");
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await page.getByLabel("生态监测任务描述").fill("分析神农溪植被变化并验证发布失败重试");
  await page.getByRole("button", { name: "创建监测任务" }).click();
  await expect(page.getByRole("status").filter({ hasText: "任务已创建" })).toBeVisible();
  const taskId = new URL(page.url()).searchParams.get("task_id");
  expect(taskId).toMatch(/^[0-9a-f-]{36}$/);

  await expect(page.getByText("任务失败", { exact: true })).toBeVisible({ timeout: 30_000 });
  const failure = page.getByRole("alert").filter({ hasText: "发布 Agent" });
  await expect(failure).toContainText("发布地图与报告");
  await expect(failure).toContainText("Publisher Agent返回了失败结果");
  await expect(page.getByText("可以从安全检查点重试")).toBeVisible();
  await expect(page.getByText("质量结论：通过", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "下载中文 PDF 报告" })).toHaveCount(0);

  await page.getByRole("button", { name: "重试失败任务" }).click();
  await expect(page.getByText("第 2 次执行", { exact: true })).toBeVisible();
  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("已复用", { exact: true })).toHaveCount(3);
  await expect(page.getByText("质量结论：通过", { exact: true })).toBeVisible();

  const snapshotResponse = await page.request.get(
    `http://127.0.0.1:8000/api/v1/tasks/${taskId ?? "missing"}`,
  );
  expect(snapshotResponse.status()).toBe(200);
  const snapshot = (await snapshotResponse.json()) as {
    current_attempt: number;
  };
  expect(snapshot.current_attempt).toBe(2);

  const historyResponse = await page.request.get(
    `http://127.0.0.1:8000/api/v1/tasks/${taskId ?? "missing"}/events`,
  );
  expect(historyResponse.status()).toBe(200);
  const historicalEvents = (await historyResponse.text())
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => JSON.parse(line.slice(6)) as { agent: string; attempt: number; status: string });
  expect(historicalEvents).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ agent: "publisher", attempt: 1, status: "FAILED" }),
    ]),
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

async function armPublisherFailures(page: Page, failures: 0 | 1): Promise<void> {
  const response = await page.request.put(
    "http://e2e-support:8999/internal/e2e/v1/publisher-failure",
    {
      headers: { "X-E2E-Control": "deterministic-e2e-control" },
      data: { failures },
    },
  );
  expect(response.status()).toBe(204);
}
