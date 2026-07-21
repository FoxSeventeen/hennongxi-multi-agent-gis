import { expect, test } from "./fixtures.js";

test("Compose 页面显示完整系统已就绪且浏览器控制台干净", async ({
  page,
  browserMessages,
}) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "神农溪生态监测指挥台" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "系统已就绪" })).toBeVisible();
  await expect(page.getByLabel("系统环境：环境可用")).toBeVisible();
  await expect(page.getByRole("button", { name: "创建监测任务" })).toBeEnabled();
  expect(browserMessages).toEqual([]);
});
