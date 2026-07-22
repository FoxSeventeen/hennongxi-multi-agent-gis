import { expect, test } from "./fixtures.js";

test("Compose 页面显示完整系统已就绪且浏览器控制台干净", async ({
  amapNetwork,
  page,
  browserMessages,
}) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "神农溪生态监测指挥台" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "系统已就绪" })).toBeVisible();
  await expect(page.getByLabel("系统环境：环境可用")).toBeVisible();
  await expect(page.getByRole("button", { name: "创建监测任务" })).toBeEnabled();
  await expect(
    page.getByRole("group", { name: "高德普通道路位置参考，神农溪区域" }),
  ).toBeVisible();
  await expect(page.getByText("高德位置参考", { exact: true })).toBeVisible();
  expect(amapNetwork.requests).toEqual([
    expect.stringMatching(
      /^https:\/\/webapi\.amap\.com\/maps\?callback=___onAPILoaded&v=2\.0&key=deterministic-e2e-js-api-key&plugin=$/,
    ),
  ]);
  expect(browserMessages).toEqual([]);
});

test("高德网络不可用时五秒回退离线图且不阻断任务主链", async ({
  amapNetwork,
  page,
  browserMessages,
}) => {
  amapNetwork.setMode("offline");
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.getByText("地图已就位", { exact: true })).toBeVisible({ timeout: 7_000 });
  await expect(page.getByText("高德位置参考", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "创建监测任务" })).toBeEnabled();
  expect(amapNetwork.requests).toHaveLength(1);

  await page.getByLabel("生态监测任务描述").fill("验证高德断网时神农溪任务仍可完成");
  await page.getByRole("button", { name: "创建监测任务" }).click();
  await expect(page.getByRole("status").filter({ hasText: "任务已创建" })).toBeVisible();
  await expect(page.getByText("任务已完成", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "NDVI 差值" })).toBeVisible();

  expect(
    browserMessages.filter(
      (message) =>
        message.startsWith("pageerror:") ||
        message.includes("Unhandled") ||
        message.includes("Uncaught"),
    ),
  ).toEqual([]);
});
