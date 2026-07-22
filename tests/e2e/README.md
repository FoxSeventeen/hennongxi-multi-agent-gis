# Compose 浏览器验收

在仓库根目录执行：

```bash
./tests/e2e/run.sh
```

该命令会重建相关镜像、等待隔离的 `hennongxi-e2e` Compose 栈就绪，并以单 Worker
Chromium 运行 5 条中文旅程。验收使用确定性小型 GIS 数据、假大模型、测试专用位置校验器和
占位 Web端凭据；Playwright 拦截全部高德域名请求，以假 Loader 验证上下文地图/成果切换，
并以主动阻断验证 5 秒回退和任务完整完成。它不会调用真实高德服务，也不读取本地真实 Key。

成功结果应为 `5 passed`。占位 Key/安全码只存在于 E2E Compose 覆盖文件，不是可用凭据，不能
复制到日常 `.env`。这套测试不能替代独立 Web端（JS API）Key 与安全密钥的真实在线浏览器冒烟。

## 全新与温缓存复跑

全新 volume 验收前先执行：

```bash
docker compose -p hennongxi-e2e -f docker-compose.yml -f tests/e2e/compose.yml down --volumes --remove-orphans
./tests/e2e/run.sh
```

随后不清理 volume，再次执行 `./tests/e2e/run.sh` 即为温缓存复跑。

## 失败诊断

失败时不会自动删除 Compose 栈，并保留以下 Git 忽略内容：

- `tests/e2e/test-results/`：失败截图、trace、浏览器日志与 `compose.log`；
- `tests/e2e/playwright-report/`：可离线查看的 HTML 报告。

可继续执行以下命令查看实时服务状态和日志：

```bash
docker compose -p hennongxi-e2e -f docker-compose.yml -f tests/e2e/compose.yml ps
docker compose -p hennongxi-e2e -f docker-compose.yml -f tests/e2e/compose.yml logs --tail=200
```
