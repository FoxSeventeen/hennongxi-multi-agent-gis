# 本地开发与运行时验证

所有命令从仓库根目录执行。后端固定 Python 3.12；首次安装见 `docs/dependencies.md`。

## 独立启动 Agent

以下命令分别在五个终端中运行；每个模块创建独立 FastAPI 实例，不导入其他 Agent 的应用代码。

```bash
uv run --frozen uvicorn hennongxi_master.main:app --host 127.0.0.1 --port 8000
uv run --frozen uvicorn hennongxi_data_agent.main:app --host 127.0.0.1 --port 8001
uv run --frozen uvicorn hennongxi_analysis_agent.main:app --host 127.0.0.1 --port 8002
uv run --frozen uvicorn hennongxi_quality_agent.main:app --host 127.0.0.1 --port 8003
uv run --frozen uvicorn hennongxi_publisher_agent.main:app --host 127.0.0.1 --port 8004
```

每个 Agent 暴露私网本地存活路由：

```bash
curl -H 'X-Correlation-ID: dddddddd-dddd-4ddd-8ddd-dddddddddddd' \
  http://127.0.0.1:8000/internal/v1/health
```

响应必须是版本 `1.0` 的 `ServiceHealth`，并在响应头和两条 JSON 请求日志中回显同一个 `X-Correlation-ID`。HTTPX、HTTP Core 和 Uvicorn access logger 已禁用 INFO 访问日志，避免查询串中的密钥或用户输入绕过结构化日志策略。

Master 另外聚合全部运行时依赖；配置就绪接口只返回安全的布尔值、阻塞码和固定消息，不返回密钥、私有 URL 或本地路径：

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/config/readiness
```

## 契约与质量门

```bash
uv run --frozen pytest -q
uv run --frozen ruff check packages services tests
uv run --frozen ruff format --check packages services tests
uv run --frozen mypy \
  packages/contracts/src \
  packages/contracts/scripts \
  packages/observability/src \
  services/master/src \
  services/data_agent/src \
  services/analysis_agent/src \
  services/quality_agent/src \
  services/publisher_agent/src
uv run --frozen openapi-spec-validator docs/openapi.yaml
uv lock --check --python 3.12
```

更新共享模型或路由后重新生成 OpenAPI，并运行契约测试；测试会拒绝未同步的文档：

```bash
uv run --frozen python packages/contracts/scripts/generate_openapi.py
uv run --frozen pytest packages/contracts/tests -q
```

## Compose 运行时

先复制本地配置并启动 OrbStack/Docker 后端；不要把真实密钥提交到 Git：

```bash
cp .env.example .env
docker compose config --quiet
docker compose up --build --detach --wait
```

Web、Master 和 Publisher 分别只绑定到宿主回环地址的 `3000`、`8000` 和 `8004`。Data、Analysis、Quality、PostGIS 与 Redis 只连接 `internal: true` 的私网，不发布宿主端口。

T04 的完整门禁会构建默认 `linux/arm64` 镜像，检查服务健康、宿主端口、私网 Agent 可达性，然后执行一次不带 `-v` 的 `down/up` 并验证 PostGIS、Redis、数据缓存和成果卷仍存在：

```bash
python scripts/compose_smoke.py
```

脚本成功后保留运行中的栈，并清理自身的持久化标记。只有目标机上的该脚本真实通过后，才能勾选 G4/T04；静态 Compose 测试不能代替容器验收。

## PostGIS 迁移与持久化仓储

数据库迁移只允许向前执行。`downgrade()` 会主动报错；已经共享或部署的迁移文件不得改写，后续模型变化必须新增 revision。首次启动或拉取新 revision 后执行：

```bash
docker compose run --rm master-agent alembic upgrade head
docker compose run --rm master-agent alembic current --check-heads
```

连接串只从 `DATABASE_URL` 读取。Alembic 配置、源码和日志中不得写入真实口令。Master 仓储使用每次操作一个异步会话；任务状态、attempt、step、事件及事件产物关联在同一事务提交，异常时整体回滚。step 依赖必须引用同一 task/attempt 中已存在的 step。事件读取使用 `after_sequence` 游标，每批限制为 1–1000 条，并批量加载该批次的产物。LLM 记录只持久化模型名、耗时、token 数、状态、响应摘要和安全错误码，不保存 API Key、原始 prompt 或原始响应正文。

T06 的真实 PostGIS 验证命令如下：

```bash
docker compose run --rm master-agent \
  pytest services/master/tests/integration/test_migration.py -q
docker compose run --rm master-agent \
  pytest services/master/tests/integration/test_repository.py -q
```

仓储测试会覆盖完整任务图重建、非法状态的原子回滚、数据库约束拒绝，以及 Worker 租约的独占、续租、过期接管和陈旧租约保护。若需要证明空库升级，应创建一次性数据库、执行 `alembic upgrade head` 和上述测试，然后删除该临时数据库；不要对已有开发库执行降级或手工改写 `alembic_version`。

## Redis 事件缓存与持久回放

Master 的 `EventStore` 先在 PostGIS 的同一事务中提交任务状态和不可变事件，再把完整 `TaskEvent` JSON 复制到按任务隔离的 Redis Stream。Stream ID 直接使用数据库生成的 `{sequence}-0`，并通过精确 `MAXLEN` 限制每个任务的缓存条目。Redis 写入失败只会让追加结果返回 `cached=false`，不会回滚已经提交的工作流事实，也不会把任务伪装为成功。

回放使用排他的 `after_sequence` 游标，每批只允许 1–1000 条。实现会先读取数据库事实，再读取并验证 Redis 条目；只有缓存批次与数据库批次完全一致时才返回缓存结果。Redis 不可用、条目损坏、缓存被裁剪或清空时，一律返回 PostGIS 中的完整批次。T17 的 SSE 只能复用这个边界，不得自行把 Redis 当作事实来源。

应用默认使用 `REDIS_URL` 指向的数据库；事件集成测试把路径替换为 Redis DB 15，并在测试前后只清空该测试库。验证命令为：

```bash
docker compose run --rm master-agent \
  pytest services/master/tests/integration/test_event_store.py -q
```

真实缓存丢失验收还要执行一次 Redis 重启并清空 DB 15，再从新的 Master 进程调用 `EventStore.replay()`；预期来源为 `DURABLE`，数据库事件顺序和任务状态保持不变。该操作只允许使用专用测试库，禁止清空应用 Redis 数据库。

## Data Agent 数据准备

Data Agent 只接受共享契约定义的五个逻辑数据 ID：`watershed`、`before_red`、`before_nir`、`after_red`、`after_nir`。请求必须同时包含 `task_id`、固定的 `prepare_data` 步骤、attempt 和 correlation ID；额外路径字段、未知 ID 或不完整集合会在 HTTP 边界返回结构化 422。服务不会把清单路径、缓存路径或上游来源 URL 放入响应。

轻量的已批准清单和完整流域边界随后端镜像打包；四个约 161 MB 的栅格仍位于 Git 忽略的 `data/cache/demo/`，并复制到项目 `data-cache` 命名卷。首次准备或重建空卷时执行：

```bash
docker compose up --build --detach --wait data-agent
docker compose cp data/cache/demo/before_red.tif data-agent:/data/cache/before_red.tif
docker compose cp data/cache/demo/before_nir.tif data-agent:/data/cache/before_nir.tif
docker compose cp data/cache/demo/after_red.tif data-agent:/data/cache/after_red.tif
docker compose cp data/cache/demo/after_nir.tif data-agent:/data/cache/after_nir.tif
```

只能复制通过 `data/manifest.json` 校验的这四个文件；不得把任意用户或模型路径映射到卷内。每次准备请求都会重新核对文件大小、SHA-256、CRS、边界、分辨率、nodata、数据类型、完整流域覆盖、有效像元比例和四栅格对齐。完整成功后才返回无路径的 `DataPrepareResult`；缺失、损坏、不对齐或覆盖不足统一返回 `DATA_INVALID`（409），清单不可用返回 `DEPENDENCY_UNAVAILABLE`（503），不会生成部分资产元数据。

服务内测试和真实私网契约测试分别执行：

```bash
docker compose run --rm data-agent \
  pytest services/data_agent/tests -q
docker compose run --rm master-agent \
  pytest services/data_agent/tests/integration/test_network.py -q
```

第二条命令从 Master 容器通过 `http://data-agent:8001` 调用内部端点，同时验证真实缓存的 2024-08-12 日期、关联 ID、响应契约和路径注入拒绝。Data Agent 没有宿主端口，跨 Agent 调用不得改成进程内导入。

## Analysis Agent 确定性栅格核心

T09 只提供 Analysis Agent 内部的栅格 I/O 与纯计算函数，不新增 HTTP 路由，也不写入成果卷。裁剪函数接收已经打开的栅格和带明确 CRS 的完整流域几何；几何先转换到栅格 CRS，再由 Rasterio 掩膜和裁剪。输出对象保留 CRS、transform、shape、派生 bounds 与有限 nodata。红光、近红外、前后时相只要 CRS、transform 或 shape 任一不同就立即拒绝，不允许仅凭数组维度相同推定空间对齐。

NDVI 固定使用浮点公式 `(NIR - Red) / (NIR + Red)`；来源掩膜、非有限值和零分母像元全部标为无效。变化值固定为 `after - before`。变化分级采用项目策略阈值 `±0.10`：`<= -0.10` 为下降（`-1`），`>= 0.10` 为上升（`1`），中间为稳定（`0`），无效类别值为 `-128`。阈值保存在分类结果中，面积统计只能沿用该值，不能单独传入另一个阈值造成标注不一致。

面积统计只接受投影 CRS。单像元面积按完整仿射矩阵行列式 `abs(a*e - b*d)` 计算，再根据 CRS 线性单位系数换算为平方米；有效面积必须等于下降、稳定、上升三类面积之和。生成式微型栅格测试对面积使用 `1e-9` 平方米的绝对容差，测试数据在运行时创建，不签入随机值或预制 NDVI 成果。

验证命令如下：

```bash
docker compose run --rm analysis-agent \
  pytest services/analysis_agent/tests/unit -q \
  --cov=hennongxi_analysis_agent \
  --cov-branch --cov-report=term-missing --cov-fail-under=90
```

该命令覆盖 NDVI 数值、掩膜传播、零分母、网格错配、跨 CRS 完整流域裁剪、地理参考保持、分级边界、旋转/错切像元面积和地理 CRS 拒绝。T10 才负责受约束 HTTP 命令、任务级原子 GeoTIFF/JSON 写入与成果校验。

## Analysis Agent HTTP 与原子成果

Analysis Agent 的私网端点为 `POST /internal/v1/analysis/run`。请求体必须符合共享 `AnalysisRunCommand`，同时携带 UUID 格式的 `Idempotency-Key` 与 `X-Correlation-ID` 头；关联头必须和请求体中的 `correlation_id` 一致。服务只接受 Data Agent 返回的五个逻辑资产引用，会重新对照受控清单、只读缓存中的文件大小、SHA-256 和完整栅格网格；请求不能提供任何文件路径。

每个 task/attempt 只允许一个 Analysis 成果集。五个固定成果先写入同一命名卷内的隐藏 staging 目录，依次关闭、刷新并校验后，再通过同文件系统目录替换一次性发布；收据最后写入。异常、进程崩溃或校验失败不会产生可见的 `analysis/` 完整目录，下一次同 attempt 执行会在持锁状态下清理残留 staging。相同幂等键只返回 checksum 仍有效的既有结果；不同幂等键不能覆盖已发布 attempt。

公开的成果元数据仅包含 UUID、类型、状态、媒体类型、UTC 时间、SHA-256 和字节数，不包含卷路径。正常日志使用 `analysis_started`、`analysis_completed`、`analysis_reused` 和 `analysis_failed` 事件，并携带 task、step、attempt、correlation、耗时和成果数量；结构化错误与意外错误响应不会回显请求体、异常消息或私有路径。

服务测试和真实私网/成果卷检查命令如下：

```bash
docker compose run --rm analysis-agent \
  pytest services/analysis_agent/tests -q

docker compose run --rm --no-deps \
  --env DATA_AGENT_BASE_URL=http://data-agent:8001 \
  --env ANALYSIS_AGENT_BASE_URL=http://analysis-agent:8002 \
  analysis-agent \
  pytest services/analysis_agent/tests/integration/test_analysis_network.py -q
```

第二条命令先通过 Data Agent 私网接口取得真实 2019-08-19/2024-08-12 资产元数据，再调用 Analysis Agent 两次。测试会验证首次生成与第二次幂等复用，并从成果卷重新打开四个 GeoTIFF 和面积统计 JSON，核对 CRS、bounds、nodata、尺寸、阈值、有效像元和 SHA-256。Analysis Agent 没有宿主端口；Master 后续只能通过 Compose 私网调用该接口。
