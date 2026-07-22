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

## 真实大模型冒烟

真实冒烟只在操作员显式执行以下命令时联网，不属于默认测试套件：

```bash
docker compose run --rm --build --no-deps master-agent \
  python -m hennongxi_master.llm_smoke
```

命令使用固定中文监测请求，只输出供应商源指纹、模型、任务/计划标识、耗时、状态、
令牌数、响应 SHA-256 和固定步骤种类。输出不包含 API Key、Authorization、Base URL、
供应商请求 ID、原始响应或模型生成的步骤标题。退出码 `0` 表示真实计划通过白名单校验，
`1` 表示已脱敏的供应商/计划错误，`2` 表示配置缺失，`3` 表示未分类的内部错误。

## 真实高德研究区校验冒烟

Master 的高德 Web 服务调用只在操作员显式执行冒烟命令或创建真实任务时发生，默认单元测试
全部使用本地假响应。创建已被
`.gitignore` 覆盖的 `.env.amap.local`，只在其中配置 Web 服务 Key：

```dotenv
AMAP_WEB_SERVICE_KEY=请替换为本机高德Web服务Key
AMAP_TIMEOUT_SECONDS=3
```

不要配置自定义域名；适配器固定访问 `https://restapi.amap.com/v3/place/text`，只发送允许表中的
规范名称、巴东县 `adcode` 和风景名胜类别，不发送用户提示词、任务标识、影像或成果信息。
显式冒烟命令为：

```bash
docker compose --env-file .env --env-file .env.amap.local \
  run --rm --build --no-deps master-agent \
  python -m hennongxi_master.amap_smoke
```

退出码 `0` 表示唯一规范结果通过，`1` 表示零/多结果或已脱敏的高德错误，`2` 表示本地配置
缺失，`3` 表示未分类内部错误。输出只含固定提供商源指纹、脱敏状态、检查时间、耗时、是否
可重试、匹配数量；高德成功响应还显示 `infocode=10000`。输出不含 Key、完整 URL、POI、
坐标、地址、原始响应或高德错误正文，代码也不缓存或持久化这些数据。

Key 必须在高德控制台创建为“Web 服务”类型并具有 POI 搜索权限；`10001`、`10002`、
`10009` 分别通常表示 Key 无效、服务无权限和平台类型不匹配。查询参数、`citylimit` 与
`adcode` 规则见[高德 POI 搜索文档](https://lbs.amap.com/api/webservice/guide/api/search/)，错误
分类见[高德 Web 服务错误码](https://lbs.amap.com/api/webservice/guide/tools/info/)。高德返回的
GCJ-02 坐标不得与本项目 G2 的 WGS84/Web Mercator 边界和遥感成果直接叠加；本适配器不会
返回或使用坐标。

### T24 规划接入与显式全链验收

Master 会先用本地允许别名判断研究区，再在进入 `PLANNING` 前执行可选在线交叉校验。
明确的外地区域在任务创建前返回中文范围错误；规范内任务在高德未配置、超时、限流或不可用
时产生 `DEGRADED` 事件并继续使用 G2 本地数据。事件只保留结论、检查时间、耗时和脱敏原因
码，不保留高德响应。

日常测试不会访问真实高德。下面的显式测试会清空开发测试库中的任务/流域记录，因此只可在
专用开发 Compose 数据库中执行。为避免后台 Worker 抢占测试任务，先暂停 Master；无论测试
是否成功，最后都要重新启动它：

```bash
docker compose stop master-agent
docker compose --env-file .env --env-file .env.amap.local \
  run --rm --no-deps -e RUN_LIVE_AMAP_INTEGRATION=1 master-agent \
  pytest \
  services/master/tests/integration/test_orchestration.py::test_verified_and_degraded_grounding_keep_fixed_agent_results_identical \
  services/master/tests/integration/test_orchestration.py::test_live_amap_grounding_completes_fixed_agent_chain \
  -q
docker compose start master-agent
```

第一个测试使用本地可控的在线通过/限流证据完成两次固定 Agent 链并比较确定性成果；第二个
测试才会使用真实高德 Key。若缺少显式开关或 Key，真实测试会跳过。两者都不把用户提示词
发送给高德，也不把 Key、POI、坐标、地址或原始响应写入任务事件。

### T25 确定性完整 Agent 链

T25 使用运行时生成的 4×4 米制 GeoTIFF、完整小型流域、假 LLM 和假高德响应，通过 Master
的真实 Worker、PostGIS 仓储和四个 Agent 的真实 FastAPI HTTP 边界执行完整链。测试不会读取
本机 LLM/高德凭据，也不会访问外网。它以同一固定 `task_id` 分别重放在线验证通过和限流降级
两条链，比较栅格 SHA-256、面积统计、质量门禁和 PDF 正文；生成时间、实测耗时及其派生的质量
报告校验和仍按每次运行如实记录，不伪装成固定值。

测试会清空所连接数据库中的任务和流域记录，因此必须使用独立 Compose 项目。下面命令只启动
隔离 PostGIS，不启动会占用宿主端口的 Web、Master 或 Publisher；`-e ...=` 明确移除测试容器
中的真实 LLM/高德配置：

```bash
docker compose -p hennongxi-t25 build master-agent postgis
docker compose -p hennongxi-t25 up --detach --wait postgis
docker compose -p hennongxi-t25 run --no-deps --rm \
  -e LLM_API_KEY= -e LLM_BASE_URL= -e LLM_MODEL= -e AMAP_WEB_SERVICE_KEY= \
  master-agent alembic upgrade head
docker compose -p hennongxi-t25 run --no-deps --rm \
  -e LLM_API_KEY= -e LLM_BASE_URL= -e LLM_MODEL= -e AMAP_WEB_SERVICE_KEY= \
  master-agent pytest tests/integration -q
docker compose -p hennongxi-t25 down
```

预期为 `8 passed`。测试组还会验证：所有 JSON HTTP 边界直接使用共享版本化契约；非法 LLM
计划只能使用明确标记的恢复计划并持久化失败证据；非目标地区、损坏数据、不可达 Agent 和缺失
分析成果均不会进入发布完成态。若确认不再需要隔离数据库，可显式执行
`docker compose -p hennongxi-t25 down --volumes`，不得把 `--volumes` 用于日常演示项目。

## Web 高德上下文地图与 MapLibre 成果切换

高德浏览器地图使用官方 `@amap/amap-jsapi-loader` 加载 JS API 2.0。没有合法 publication 时，
页面把固定 WGS84 中心点 `[110.299073, 31.262497]` 以 `AMap.convertFrom(point, "gps")` 临时
转换，只创建默认 2D 普通道路地图；任务 ID、提示词、流域几何、栅格、统计和报告都不会进入
高德请求。合法 publication 到达后组件调用 `destroy()`，由既有 MapLibre 独占显示三类 NDVI
图层、完整流域、图例和 attribution；非法 publication 仍显示“地图图层暂不可用”。

三项配置边界必须保持独立：

- `AMAP_WEB_SERVICE_KEY` 只属于 Master 的地名校验；
- `VITE_AMAP_JS_API_KEY` 是浏览器可见的专用 Web端（JS API）Key；
- `AMAP_JS_API_SECURITY_CODE` 只由 Vite/Web 服务端读取，不能使用 `VITE_` 前缀。

Web 在加载 Loader 前把 `serviceHost` 固定为当前来源的 `/_AMapService`。代理只接受同源浏览器的
`GET /_AMapService/v3/assistant/coordinate/convert`，并固定转发到批准的高德 HTTPS 路径；
客户端不能指定 `jscode`、上游、重定向或请求头。代理还有 3 秒上游超时、256 KiB 响应上限和
8 个并发上限，错误只返回中文通用码。坐标转换使用 JSONP 时，代理只接受一个合法 JavaScript
标识符形式的 `callback`，解析上游 JSON/JSONP 后重新序列化为
`application/javascript; charset=utf-8`，并保留 `nosniff`；重复、可注入或正文不匹配的回调
都会被脱敏拒绝。不得为了调试放宽为任意路径代理。

前端回归从 `apps/web` 执行：

```bash
cd apps/web
pnpm exec vitest run
pnpm exec tsc -b --pretty false
pnpm exec eslint .
pnpm exec vite build
pnpm audit --prod --audit-level high
cd ../..
```

组件测试覆盖未配置、成功、任务更新复用、5 秒超时、Loader/转换失败、卸载竞态、成果切换和
MapLibre 回归。默认 Compose E2E 在隔离私网中使用确定性占位 Key，并用 Playwright 路由拦截
`webapi.amap.com` 后返回假 Loader；另一条旅程主动阻断该路由，验证离线占位图和任务完整完成。
运行：

```bash
./tests/e2e/run.sh
```

预期 `5 passed`。这套 E2E 不读取 `.env` 中的真实高德配置、不访问真实高德、不消耗额度，也不
证明在线地图、官方 Logo/版权或真实 Key 权限正确。真实浏览器冒烟和证据限制按
[`setup.md`](setup.md) 与 [`verification.md`](verification.md) 执行。

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
  pytest \
  services/analysis_agent/tests/unit/test_ndvi.py \
  services/analysis_agent/tests/unit/test_raster_io.py \
  services/analysis_agent/tests/unit/test_change.py -q \
  --cov=hennongxi_analysis_agent.ndvi \
  --cov=hennongxi_analysis_agent.raster_io \
  --cov=hennongxi_analysis_agent.raster \
  --cov=hennongxi_analysis_agent.change \
  --cov-branch --cov-report=term-missing --cov-fail-under=90
```

该门槛只计算规格所称的纯栅格逻辑模块，不把由 T10 集成测试负责的 HTTP、执行器和原子存储
计入分母；当前为 20 项通过、分支覆盖率 95.72%。它覆盖 NDVI 数值、掩膜传播、零分母、网格
错配、跨 CRS 完整流域裁剪、地理参考保持、分级边界、旋转/错切像元面积和地理 CRS 拒绝。
T10 才负责受约束 HTTP 命令、任务级原子 GeoTIFF/JSON 写入与成果校验。

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

## Quality Agent 独立验收与原子报告

Quality Agent 的私网端点为 `POST /internal/v1/quality/evaluate`。请求必须符合共享 `QualityEvaluateCommand`，同时携带 UUID 格式的 `Idempotency-Key` 与 `X-Correlation-ID`；关联头必须和请求体一致。命令只接受 Analysis 返回的四个栅格成果和一个面积统计成果引用，不接受任何路径字段。Quality Agent 没有宿主端口，也不导入 Data 或 Analysis Agent 的应用代码。

服务仅以只读方式挂载 Analysis 成果卷，并使用独立可写的 `quality-reports` 命名卷。它根据固定的 task/attempt 目录和固定文件名重新定位成果，拒绝符号链接，然后核对状态、媒体类型、字节数与 SHA-256。四个 GeoTIFF 会被独立重开并检查单波段、CRS、分辨率、像元对齐、nodata、有限值和值域；面积统计 JSON 必须具有精确字段集，像元计数、分类有效像元数和面积乘积必须互相一致。

质量指标的定义如下：

- 流域覆盖率为四个栅格各自覆盖率的最小值，清单批准阈值为 `>= 0.95`。
- 有效像元率为四个栅格在流域覆盖部分有效率的最小值，清单批准阈值为 `>= 0.90`。
- 输出完整性要求四个栅格和面积统计全部通过检查，即 `5/5`。
- Analysis 耗时直接记录为非负整数毫秒；当前规格没有批准最大耗时门限，因此不另行虚构阈值。

只有前三项门禁全部满足时结论才是 `PASS`，否则是 `FAIL`。响应同时给出四条中文证据，避免用单一不透明分数掩盖失败原因。质量报告先写入隐藏 staging 目录并刷新到磁盘，再以同文件系统目录替换原子发布；收据记录幂等键和结果。相同 task/attempt 与幂等键只有在报告重新校验成功后才复用，不同幂等键返回 409，已发布报告损坏也返回结构化 409。成果缺失、损坏或不符合质量约束会形成诚实的 `FAIL` 报告；批准的质量配置或报告存储不可用时返回经脱敏的 503，意外错误返回经脱敏的 500。

服务测试和真实三 Agent 私网链路分别执行：

```bash
docker compose run --rm quality-agent \
  pytest services/quality_agent/tests -q

docker compose run --rm --no-deps \
  --env DATA_AGENT_BASE_URL=http://data-agent:8001 \
  --env ANALYSIS_AGENT_BASE_URL=http://analysis-agent:8002 \
  --env QUALITY_AGENT_BASE_URL=http://quality-agent:8003 \
  quality-agent \
  pytest services/quality_agent/tests/integration/test_quality_network.py -q
```

第二条命令通过真实私网依次调用 Data、Analysis 和 Quality，并重复 Quality 请求验证幂等复用；随后从独立报告卷读取 JSON，核对 task 作用域、指标内容、字节数和 SHA-256。使用修正后的 2019-08-19/2024-08-12 Sentinel-2 缓存时，验收结果为覆盖率 `1.0000`、有效像元率约 `0.9312`、输出完整性 `5/5`、结论 `PASS`。

## Publisher 瓦片、图层元数据与 PDF 报告验证

Publisher 的公共只读路由为瓦片路由
`GET /api/v1/tiles/{task_id}/{artifact_type}/{z}/{x}/{y}.png` 和任务绑定下载路由
`GET /api/v1/tasks/{task_id}/artifacts/{artifact_id}/download`；内部编排路由为
`POST /internal/v1/publisher/publish`，必须提供 `Idempotency-Key` 和匹配命令的
`X-Correlation-ID`。发布命令重新核对同一任务/尝试的 Analysis 与 Quality 收据、质量
PASS、字节数和 SHA-256，然后生成四个图层元数据及一个原子写入的中文 PDF。下载时
再次核对任务、成果 ID、字节数和 SHA-256，并返回安全 ASCII 文件名、`application/pdf`、
`no-store` 和 `nosniff` 响应头。任何路由都不会接收或返回本地存储路径。

服务回归和真实 G2 网络验收分别执行：

```bash
docker compose run --rm publisher-agent \
  pytest services/publisher_agent/tests packages/contracts/tests \
  tests/test_compose_topology.py tests/test_service_shells.py -q

docker compose run --rm --no-deps \
  --env PUBLISHER_AGENT_BASE_URL=http://publisher-agent:8004 \
  publisher-agent \
  pytest services/publisher_agent/tests/integration/test_publisher_network.py -q
```

第二条命令要求已有真实 T11 成果并已启动 Publisher。当前批准数据的验收结果为
`2 passed`：一项断言 PNG 尺寸、透明度和固定颜色；另一项断言四个图层资源使用
2019-08-19/2024-08-12 日期、真实 WGS84 边界、匹配单位/图例和修改后 Copernicus
Sentinel 数据归属，并通过公共路由下载、解析同任务的中文 PDF。PDF 的自动测试还会
执行 pypdf 文本提取和 Poppler 两页 A4 渲染；真实 G2 报告需在 `tmp/pdfs/` 中逐页检查。
