# 目标机器安装与配置

本文供第一次接手项目的人在目标 Apple Silicon 机器上准备真实演示环境。所有命令均从仓库根目录
执行。日常快速入口见项目 [`README.md`](../README.md)，具体答辩顺序见
[`demo-runbook.md`](demo-runbook.md)。

## 1. 交付物清单

接手人应通过不同渠道拿到三类内容：

1. Git 仓库源码；
2. 不进入 Git 的四个已批准 GeoTIFF；
3. 不进入 Git 的大模型配置，以及一枚可在交接后轮换的高德 Web 服务 Key（高德可选）。

源码仓库不应包含 `.env`、真实 Key、原始/缓存影像、生成栅格、PDF、日志、Playwright 截图或
Trace。开始前执行：

```bash
git status --short --branch
git log -1 --pretty=fuller
git config user.email
```

预期工作区干净，提交者邮箱为项目所有者指定的 `1661863496@qq.com`。如果只负责演示而不修改
代码，不需要创建新提交。

## 2. 环境要求

目标组合与具体版本记录在 [`dependencies.md`](dependencies.md)：

- Apple Silicon / `linux/arm64`；
- 已启动的 OrbStack 或 Docker Desktop；
- Docker Compose 支持 `--wait`；
- Git；
- Python 3.12、`uv` 0.11 系列（运行数据预检和本地检查）；
- Node.js 24 与 pnpm 11 只在脱离容器开发 Web 时需要。

检查基础工具：

```bash
uname -m
docker version
docker compose version
python3 --version
uv --version
```

`uname -m` 应输出 `arm64`。如果 Docker 命令无法连接守护进程，先启动 OrbStack/Docker Desktop，
不要反复重建或删除 volumes。

## 3. 本地配置

创建被 Git 忽略的配置：

```bash
cp .env.example .env
git check-ignore -v .env
```

第二条命令必须显示 `.gitignore` 规则。编辑 `.env` 时保留其余本地默认值，只填写真实配置：

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | 是 | 兼容大模型接口的访问令牌，只注入 Master |
| `LLM_BASE_URL` | 是 | 兼容接口的基础 URL，必须使用可信 HTTPS 地址 |
| `LLM_MODEL` | 是 | 供应商实际支持的模型名 |
| `LLM_TIMEOUT_SECONDS` | 否 | 默认 30 秒，答辩前不要无证据地放大 |
| `AMAP_WEB_SERVICE_KEY` | 否 | 高德 Web 服务 Key，只用于研究区地名校验 |
| `AMAP_TIMEOUT_SECONDS` | 否 | 默认 3 秒，保证可选增强不会长时间阻塞 |

不要使用前端变量保存 Key，不要在终端执行带真实值的 `export ...` 命令，不要把配置粘贴到 issue、
截图、演示文稿或验收文档。Key 曾经出现在聊天、屏幕共享或其他公开位置时，交付前必须在供应商
控制台轮换；轮换后只更新本机 `.env`。

检查配置文件仍未被 Git 跟踪：

```bash
git status --short
git ls-files .env
```

第二条命令应无输出。

## 4. 准备并预检真实数据

把交付者提供的四个文件放到：

```text
data/cache/demo/before_red.tif
data/cache/demo/before_nir.tif
data/cache/demo/after_red.tif
data/cache/demo/after_nir.tif
```

不要从不明网盘、搜索结果或临时 URL 替换这些文件。执行完全离线的预检：

```bash
uv run --frozen python scripts/data_preflight.py
```

预期 19 行检查全部为 `[PASS]`，包括五项完整性、流域 GIS、四栅格元数据、覆盖率、有效像元率
和统一网格。事实来源是 [`../data/manifest.json`](../data/manifest.json)；不要手工修改清单来适配
错误文件。

如预检失败，停止安装并查看 [`troubleshooting.md`](troubleshooting.md)。答辩当天禁止运行
`scripts/cache_demo_data.py`，该脚本会访问外部数据源，且重新生成的数据需要再次审批。

## 5. 首次初始化

先解析配置并构建固定镜像：

```bash
docker compose config --quiet
docker compose build
```

只启动数据库和 Redis，再执行向前迁移：

```bash
docker compose up --detach --wait postgis redis
docker compose run --rm --no-deps master-agent alembic upgrade head
docker compose run --rm --no-deps master-agent alembic current --check-heads
```

`current --check-heads` 必须退出 0。迁移失败时不要降级、删除数据库卷或改写旧 revision；保留日志并
按排障文档处理。

创建数据缓存卷并复制四个已通过预检的文件：

```bash
docker compose up --detach --wait data-agent
docker compose cp data/cache/demo/before_red.tif data-agent:/data/cache/before_red.tif
docker compose cp data/cache/demo/before_nir.tif data-agent:/data/cache/before_nir.tif
docker compose cp data/cache/demo/after_red.tif data-agent:/data/cache/after_red.tif
docker compose cp data/cache/demo/after_nir.tif data-agent:/data/cache/after_nir.tif
```

这一步只需要在 `data-cache` 卷为空或已被明确重建时执行。重复复制批准文件是安全的，但不能复制
任意用户文件或改变卷内文件名。

## 6. 真实上游冒烟

在完整演示前分别验证上游。命令只输出脱敏证据，不输出 Key、原始提示或原始响应。

### 大模型

```bash
docker compose run --rm --no-deps master-agent \
  python -m hennongxi_master.llm_smoke
```

退出码 0 且 JSON 中 `ok=true`、`status="SUCCEEDED"` 才算通过。可以记录 `task_id`、`plan_id`、
`duration_ms`、模型名、token 数和两个 SHA-256；不要记录 `.env` 内容。退出码含义：

| 退出码 | 含义 |
| ---: | --- |
| 0 | 真实调用和计划校验通过 |
| 1 | 供应商调用或计划验证安全失败，查看脱敏 `error_code` |
| 2 | 配置缺失 |
| 3 | 冒烟程序内部错误 |

### 高德（可选）

```bash
docker compose run --rm --no-deps master-agent \
  python -m hennongxi_master.amap_smoke
```

退出码 0 且 JSON 中 `ok=true`、`code="VERIFIED"`、`infocode="10000"` 才表示在线校验通过。输出
只保留固定服务来源哈希、检查时间、耗时和匹配数量。退出码 1 表示供应商返回了安全的非通过状态，
2 表示未配置，3 表示内部错误。高德失败不等于遥感主链不可用。

冒烟会产生真实 API 调用和额度消耗，只在配置/轮换后或正式演练前执行，不要在自动化循环中运行。

## 7. 启动完整系统

```bash
docker compose up --detach --wait
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/config/readiness
```

预期：

- `docker compose ps` 中八个常驻服务均为运行/健康状态；
- 聚合健康响应的 `state` 为 `HEALTHY`；
- 就绪响应为 `ready: true`、`llm_configured: true`、`data_configured: true`、`blockers: []`；
- [http://localhost:3000](http://localhost:3000) 显示“系统已就绪”；
- 宿主机只使用 `127.0.0.1:3000`、`:8000`、`:8004`。

就绪接口验证的是配置存在、数据清单可读和运行依赖健康，不会替代真实大模型/高德冒烟，也不会
提前扫描命名卷中的四个大栅格；因此数据预检和至少一次真实任务仍然必需。

## 8. 日常启动、升级与停止

已经完成初始化的机器日常启动：

```bash
docker compose up --detach --wait
```

拉取包含新迁移的代码后，先执行：

```bash
docker compose up --detach --wait postgis redis
docker compose run --rm --no-deps master-agent alembic upgrade head
docker compose up --detach --wait
```

非破坏性停止：

```bash
docker compose down
```

严禁在演示机器上执行以下命令，除非项目所有者明确决定销毁全部本地状态且已有可验证备份：

```text
docker compose down -v
docker compose down --volumes
docker volume prune
```

这些操作会删除任务历史、数据缓存和生成成果。普通停止不需要删除任何卷。

## 9. 安装完成检查表

- [ ] Git 工作区干净，`.env` 被忽略且未被跟踪。
- [ ] 四个真实栅格在本地预检中全部通过。
- [ ] PostGIS 已迁移到 head，四个栅格已复制到 `data-cache` 卷。
- [ ] 八个常驻服务健康，聚合健康与配置就绪均通过。
- [ ] 真实大模型冒烟通过并只记录脱敏证据。
- [ ] 如答辩需要展示高德，真实高德冒烟通过且已确认当日额度/状态。
- [ ] 浏览器能访问 Web，控制台没有错误，未通过公网暴露端口。
- [ ] 已阅读演示手册和排障文档，知道如何非破坏性停止。
