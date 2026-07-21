# Compose 浏览器验收

在仓库根目录执行：

```bash
./tests/e2e/run.sh
```

该命令会重建相关镜像、等待隔离的 `hennongxi-e2e` Compose 栈就绪，并以单 Worker
Chromium 运行中文主旅程。验收使用确定性小型 GIS 数据、假大模型和测试专用位置校验器，
不会调用真实高德服务，也不读取本地真实 Key。

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
