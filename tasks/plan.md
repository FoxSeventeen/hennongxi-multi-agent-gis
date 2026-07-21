# Implementation Plan: 神农溪分布式多 Agent GIS 演示系统

## Plan status

- Baseline: approved `docs/spec.md` at commit `92e50a6` (`docs: define the approved MVP specification`).
- Repository state at planning time: greenfield; only `.gitignore` and `docs/spec.md` are tracked.
- Scope of this document: the 10-day MVP only. It does not reopen requirements discovery or add scenarios outside ecological change monitoring.
- Approval status: approved by the user on 2026-07-19, including the proposed Publisher resource routes below.
- 2026-07-21 变更已批准：在不改变 G2 权威数据、五 Agent 拓扑和公开路由集合的前提下，
  增加可降级的高德 Web 服务研究区校验；中文门禁 G5 已由用户以“开始 T23”批准。

## Overview

Build a Chinese, map-first single-page application in which a user submits a natural-language ecological monitoring request, a real configured LLM produces a schema-validated fixed plan, and five independently deployed Agent services execute a traceable NDVI workflow under one `task_id`. PostgreSQL/PostGIS is the durable source of record, Redis carries ordered progress events, the Master Agent owns public workflow APIs and orchestration, and the Publisher Agent serves computed map products and a Chinese PDF. An optional backend-only AMap Web Service check grounds the approved study-area name in its expected administrative district without becoming a raster, watershed, basemap, or offline-data dependency. The implementation is organized to fail fast on the highest-risk areas—Apple Silicon containers, authoritative local GIS data, raster math, cross-service contracts, external-service degradation, and real LLM compatibility—while leaving the repository runnable at each checkpoint.

## Planning assumptions

1. `docs/spec.md` is the authoritative product and engineering contract; later code must not silently weaken its success criteria.
2. The LLM provider, base URL, and model may remain unset during most development. Fake-server tests are mandatory, while a real smoke test is an explicit externally gated checkpoint before acceptance.
3. Raw demo imagery is cached locally and remains ignored by Git. Git stores the full watershed boundary, source metadata, acquisition dates, band mapping, checksums, and setup instructions.
4. PostgreSQL is the durable source of truth for tasks, attempts, steps, events, and artifact metadata. Redis is an event/cache transport and may be rebuilt without losing the final task record.
5. The Master starts work outside the request lifecycle and calls every Agent through versioned HTTP endpoints. A single lifespan worker atomically claims runnable rows from PostgreSQL; this durable claim/recovery mechanism avoids adding Celery or another infrastructure service.
6. Generated rasters and reports use a Docker named volume shared only where required. Services exchange commands and metadata through HTTP; no Agent imports another Agent's application code.
7. Full container verification requires the installed OrbStack/Docker-compatible backend to be running. Local unit work may proceed without it, but no container checkpoint can be marked complete without an actual `linux/arm64` run.
8. 高德只作为可选在线校验源：未配置、超时、限流或服务不可用时，不阻断规范中的神农溪
   离线演示主链，也不得把降级结果标记为“高德已验证”。
9. 高德返回内容不写入 PostGIS、Redis、成果文件或测试夹具；仅保留本系统生成的提供商无关
   校验结论、检查时间、耗时和脱敏原因码。高德 Key 仅存在于后端环境变量。

## Definition of Done

A task is complete only when all of its acceptance criteria and verification items pass. In addition:

- Relevant unit, contract, integration, lint, and type checks pass with no unrelated regressions.
- External inputs and LLM output are validated; identifiers, timestamps, errors, and logs follow the approved conventions.
- No credentials, private URLs, downloaded raw imagery, or generated outputs enter Git.
- User-visible behavior is verified at runtime, not inferred only from code or mocks.
- Documentation and environment examples change in the same task when an operator-facing contract changes.

## Architecture decisions

### Service and data boundaries

- **Master Agent:** owns task creation/query/retry, LLM adaptation, state transitions, a single database-claiming background worker, SSE fan-out, aggregate health/readiness, and durable workflow records.
- **Data Agent:** accepts dataset identifiers—not model-generated paths—validates the watershed and four red/NIR inputs, and returns a checksum-bearing manifest with common CRS/grid metadata.
- **Analysis Agent:** computes both NDVI rasters, the difference raster, classified change, and area statistics from validated inputs. Pure raster math stays independent of FastAPI for deterministic tests.
- **Quality Agent:** independently verifies coverage, valid-pixel ratio, artifact completeness, and elapsed time from artifact metadata and raster inspection.
- **Publisher Agent:** exposes read-only raster visualization, artifact downloads, and ReportLab PDF generation. It never mutates workflow state directly.
- **PostgreSQL/PostGIS:** stores watershed geometry, task/attempt/step/event rows, sanitized LLM-call metadata, and artifact metadata. Alembic owns forward migrations.
- **Redis:** uses an ordered per-task event stream plus bounded cache. The Master persists an event before/while publishing it so refresh and polling never depend on Redis alone.
- **Artifact volume:** filenames are derived from validated `task_id`, artifact type, and attempt—not LLM text or arbitrary request paths. Atomic temporary-file replacement prevents partially published outputs.

### Contract-first API

Task 02 freezes Pydantic schemas and a checked-in OpenAPI description before endpoint implementation. Public workflow routes remain exactly those approved in the specification:

- `POST /api/v1/tasks`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/{task_id}/events`
- `POST /api/v1/tasks/{task_id}/retry`
- `GET /api/v1/health`
- `GET /api/v1/config/readiness`

The browser also needs the resource delivery already required of the Publisher. The plan proposes these two read-only, versioned routes for approval with this document:

- `GET /api/v1/tiles/{task_id}/{artifact_type}/{z}/{x}/{y}.png`
- `GET /api/v1/tasks/{task_id}/artifacts/{artifact_id}/download`

Internal HTTP commands are versioned and contain `task_id`, `step_id`, `attempt`, `correlation_id`, and a constrained payload:

- `POST /internal/v1/data/prepare`
- `POST /internal/v1/analysis/run`
- `POST /internal/v1/quality/evaluate`
- `POST /internal/v1/publisher/publish`

### State, events, and retry

- The task state graph is the exact approved sequence: `PENDING -> PLANNING -> DATA_PREPARING -> ANALYZING -> QUALITY_CHECKING -> PUBLISHING -> COMPLETED`; any active state can become `FAILED`.
- Events have a monotonic database sequence, UTC timestamp, task/step/attempt/correlation identifiers, Agent, state, progress, message, elapsed time, optional structured error, and optional artifact references.
- SSE supports `Last-Event-ID`; the Master replays durable missed events and then tails Redis. The Web falls back to bounded HTTP polling after disconnect or unsupported streaming.
- Retry creates a new attempt at the failed safe checkpoint. It reuses only upstream artifacts whose checksums and completeness still validate, resets the failed and downstream steps, and never rewrites prior attempt history.
- Master startup marks/requeues recoverable nonterminal tasks according to their last durable checkpoint so a process restart does not falsely report success.

### LLM safety boundary

- The adapter is provider-compatible HTTP configured only by `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`.
- Model output is parsed into a versioned Pydantic plan and matched against the ecological-monitoring step whitelist; shell, SQL, Python, URLs, and arbitrary paths are not executable fields.
- Logs persist sanitized provider/model/timing/status/token metadata and a response hash, never the key or authorization header.
- A deterministic built-in plan is visibly labeled as recovery output and cannot satisfy the real-LLM acceptance checkpoint.

### GIS processing and presentation

- NDVI uses floating-point `(NIR - Red) / (NIR + Red)`, masks nodata/non-finite/zero-denominator pixels, clips to the complete watershed, and writes georeferenced compressed GeoTIFF artifacts.
- Inputs must be explicitly aligned to a common CRS, transform, resolution, and extent before comparison; silent shape-only alignment is rejected.
- Area statistics use pixel area in an appropriate projected CRS and a documented change-class threshold set.
- Rio-Tiler reads only allow-listed artifacts to render Web Mercator PNG tiles; MapLibre overlays these tiles with the complete watershed vector boundary.
- ReportLab embeds a redistributable Chinese font inside the image, and PDF tests verify text extraction plus rendered-page integrity.

### 高德 Web 服务采用边界（G5）

高德最适合补足现有 Master 对自然语言研究区的校验缺口，而不是替换遥感 GIS 主链。

| 现有组件 | 可改进点 | 本轮决定 |
| --- | --- | --- |
| Master Agent | 当前任意查询都会绑定固定神农溪流域；增加地名消歧、巴东县行政区匹配和可观测降级 | 接入，且只允许固定 HTTPS 高德域名 |
| Data Agent | 当前只校验固定本地数据，不知道用户地名是否与神农溪一致 | 受益于 Master 前置门禁；接口、私网隔离和数据清单不变 |
| Quality Agent | 可把“研究区意图与批准数据一致”作为补充运行证据 | 本轮只通过 Master 事件呈现，不修改四项核心质量模型 |
| Analysis Agent | NDVI 需要真实红光/近红外栅格和严格网格/坐标处理 | 不接入；高德不作为遥感输入或统计依据 |
| Publisher/Web | 可解释“请求位置已校验/在线校验已降级” | 复用现有任务事件与结果文案，不新增公开路由，不展示或缓存高德原始数据 |
| MapLibre 地图 | 高德可提供底图，但与现有 WGS84/Web Mercator 成果存在坐标系和授权边界 | MVP 不替换、不混合底图；继续使用现有 MapLibre 与自有成果瓦片 |

实施约束：

- 使用已验证的 Web 服务 Key，通过地理编码或限定 `adcode` 的 POI 搜索核验
  “神农溪/巴东县”语义；不加入路径规划、生活 POI、天气或第二个 GIS 场景。
- 只向高德发送本地允许表中的固定规范名称和巴东县 `adcode`，不发送用户完整提示词、
  `task_id`、关联标识、影像/成果信息或其他业务数据。
- 高德采用 GCJ-02。现有 G2 边界、遥感栅格和 Publisher 瓦片仍保持原坐标契约；不得把
  高德坐标直接与 WGS84 几何叠加或据此修改分析成果。
- 不抓取、下载、镜像、缓存或持久化高德 POI、行政区边界、底图、坐标或原始响应；测试使用
  本地假服务构造的自有夹具，真实调用仅是显式、低频、脱敏的冒烟检查。
- `AMAP_WEB_SERVICE_KEY` 仅注入 Master；固定目标域名、连接/读取超时、响应大小上限、严格
  JSON 校验和状态码映射。Key、完整 URL 查询串和 `Authorization` 等敏感信息不得进入日志。
- 明确属于其他地区的请求按既有单场景范围返回中文校验错误；规范内的神农溪请求在高德故障
  时使用 G2 本地权威数据继续执行，并明确显示“在线位置校验已降级”。

官方依据：

- 地理/逆地理编码：<https://lbs.amap.com/api/webservice/guide/api/georegeo/>
- POI 搜索及 `adcode` 限定：<https://lbs.amap.com/api/webservice/guide/api/search/>
- 行政区域查询：<https://lbs.amap.com/api/webservice/guide/api/district/>
- Web 服务错误码：<https://lbs.amap.com/api/webservice/guide/tools/info/>
- GCJ-02 与 WGS84 转换说明：<https://lbs.amap.com/api/javascript-api-v2/guide/transform/convertfrom>
- 高德开放平台服务协议：<https://lbs.amap.com/pages/terms/>

## Dependency graph and critical path

```text
T01 dependency baseline
  -> T02 contracts/OpenAPI
      -> T03 service shells
          -> T04 Compose topology
      -> T06 persistence
      -> T07 event stream
      -> T08 Data Agent --\
                         -> T10 Analysis Agent -> T11 Quality Agent --\
      -> T09 NDVI core --/                    -> T12 tile publishing --> T13 PDF report
      -> T14 LLM adapter -> T15 public task API
          -> T16 orchestration -> T17 SSE -> T18 retry/recovery
              -> T19 Web shell -> T20 timeline -> T21 map -> T22 results/retry UI
                  -> T23 AMap adapter -> T24 study-area grounding
                      -> T25 contract/integration suite -> T26 Compose/E2E
                          -> T27 hardening -> T28 rehearsal/handoff

T05 authoritative data/cache feeds T08, T09, T24-T26, and T28.
T04 Compose topology feeds every container-level checkpoint from T10 onward.
```

The critical path is contracts → persistence/events → raster chain → orchestration → live Web flow → container E2E → rehearsal. After Task 02, frontend shell work and GIS pure-logic work may be developed independently, but shared schemas must not be duplicated.

## 10-day execution map

| Day | Planned tasks | Demonstrable outcome |
| --- | --- | --- |
| 1 | T01-T03 | Pinned toolchain, frozen contracts, five independent service shells |
| 2 | T04-T06 | ARM64 Compose topology, local data manifest, durable task model |
| 3 | T07-T09 | Ordered events, Data validation, tested NDVI math |
| 4 | T10-T12 | Real raster analysis, quality metrics, map tiles |
| 5 | T13-T15 | Chinese PDF, validated LLM plan, public task API |
| 6 | T16-T18 | End-to-end orchestration, live events, honest failure/retry |
| 7 | T19-T20 | Chinese task/readiness UI and live Agent timeline |
| 8 | T21-T22 | Interactive map, metrics, downloads, retry UX |
| 9 | T23-T25 | 高德适配与研究区校验、确定性全链契约测试 |
| 10 | T26-T28 | Compose 浏览器旅程、加固、真实演练与可复现交付 |

## Detailed task list

### Phase 1: Contract and runnable foundation

#### Task 01: Pin the ARM64-compatible toolchain and configuration contract

**Description:** Verify current official Python, Node, FastAPI/Pydantic, GIS, database, Redis, React/MapLibre, testing, and container documentation before pinning application dependencies and documenting required environment variables. No provider-specific LLM SDK is added.

**Acceptance criteria:**

- [ ] Python/Node dependencies and base images are pinned with an Apple Silicon compatibility note and license/font constraints recorded.
- [ ] `.env.example` contains safe placeholders for LLM, database, Redis, data, and artifact settings; configuration fails clearly without leaking secrets.
- [ ] One documented command installs/checks each backend and frontend dependency set reproducibly.

**Verification:**

- [ ] Dependency lock/metadata checks succeed in clean backend and frontend environments.
- [ ] `git diff --check` passes and a secret-pattern review finds placeholders only.

**Dependencies:** None.

**Files likely touched:** `pyproject.toml` plus backend lock, `apps/web/package*.json`, `.env.example`, `docs/dependencies.md`.

**Estimated scope:** M (configuration-focused; split backend and frontend lock commits if the diff grows).

#### Task 02: Freeze shared schemas, state transitions, and OpenAPI contracts

**Description:** Define the versioned Pydantic request/response/event/artifact/plan models, explicit transition table, structured errors, public and internal HTTP contracts, and the two proposed Publisher resource routes before endpoint logic exists.

**Acceptance criteria:**

- [ ] Every public/internal payload carries the required identifiers and rejects unknown executable steps, invalid progress, illegal transitions, and unsafe path-like model fields.
- [ ] Checked-in OpenAPI documents all approved workflow routes, proposed Publisher resource routes, status codes, SSE event shape, and structured errors.
- [ ] Schema/transition tests cover valid round trips and representative invalid boundaries.

**Verification:**

- [ ] `pytest packages/contracts/tests -q`
- [ ] OpenAPI validation/lint command selected in T01 passes.

**Dependencies:** T01.

**Files likely touched:** `packages/contracts/`, `packages/contracts/tests/`, `docs/openapi.yaml`.

**Estimated scope:** M.

#### Task 03: Create independent Agent shells and correlation-aware logging

**Description:** Create separately importable FastAPI applications for Master, Data, Analysis, Quality, and Publisher, using a small shared observability package for JSON logs and correlation propagation. Each exposes only a local health endpoint until its feature task lands.

**Acceptance criteria:**

- [ ] Five distinct apps start on ports 8000-8004 and report service name/version/health without importing another Agent app.
- [ ] Incoming or generated correlation IDs appear in structured logs and outbound HTTP headers.
- [ ] App startup/shutdown tests close HTTP and storage resources cleanly.

**Verification:**

- [ ] `pytest services packages/observability/tests -q -k 'health or correlation or lifespan'`
- [ ] Ruff and Python type checks pass for the new packages.

**Dependencies:** T01, T02.

**Files likely touched:** `services/*/app/main.py`, `packages/observability/`, shared test helpers.

**Estimated scope:** M.

#### Checkpoint A: Foundation contract

- [ ] T01-T03 verification passes from a clean environment.
- [ ] OpenAPI and shared schemas agree, and the Publisher resource routes receive plan approval.
- [ ] A reviewer can start each Agent independently and correlate one test request in logs.

### Phase 2: Runtime, data, and durable state

#### Task 04: Assemble the Docker Compose runtime and readiness aggregation

**Description:** Add Linux/ARM64-compatible images, Compose networking, health checks, named volumes, PostGIS, Redis, the five Agent containers, and a minimal Web container. Only Web, Master, and Publisher resource ports are host-visible.

**Acceptance criteria:**

- [x] `docker compose config` shows all required services, correct internal ports, health dependencies, named volumes, and no accidental host exposure for internal Agents/PostGIS/Redis.
- [x] `docker compose up --build` starts the shells on Apple Silicon; Master health distinguishes liveness from aggregate dependency readiness.
- [x] Stopping without `-v` preserves database and cached artifact volumes.

**Verification:**

- [x] `docker compose config --quiet`
- [x] Container smoke script checks ports, health, network-only Agent access, and restart persistence.

**Dependencies:** T03.

**Files likely touched:** `docker-compose.yml`, `infra/docker/`, service Dockerfiles, `apps/web/Dockerfile`, `tests/smoke/`.

**Estimated scope:** M.

#### Task 05: Select and cache authoritative demonstration data

**Description:** Verify official/authoritative sources, select the complete Shennong Stream watershed boundary and two dated red/NIR acquisitions, record provenance and checksums, and provide an idempotent local-cache procedure. Raw scenes stay outside Git and the final demo never downloads at runtime.

**Acceptance criteria:**

- [x] `data/manifest.json` records source organization/URL, license, acquisition date, bands, CRS, resolution, nodata, local logical IDs, file sizes, and checksums.
- [x] The tracked boundary is the complete watershed and both image dates cover it sufficiently for the approved quality metric.
- [x] A preflight command verifies cache presence/checksums and explains remediation without printing private URLs.

**Verification:**

- [x] Manifest schema and checksum/preflight tests pass.
- [x] A GIS inspection command confirms readable CRS, bounds, band mapping, and watershed overlap for all four inputs.

**Dependencies:** T01. Blocks T08, T09, T24-T26, and T28.

**Files likely touched:** `data/manifest.json`, `data/boundaries/`, `data/samples/README.md`, `scripts/data_preflight.py`, tests.

**Estimated scope:** M; source choice requires explicit approval before download/use.

#### Task 06: Implement PostGIS migrations and the durable task repository

**Description:** Create forward-only migrations and an async repository for watershed geometry, tasks, attempts, worker claims, steps, ordered events, sanitized LLM-call metadata, and artifacts. Enforce legal identifiers/states and useful indexes at the database boundary.

**Acceptance criteria:**

- [x] A fresh database migrates to head and stores one complete task/attempt/step/event/artifact graph with UTC timestamps and PostGIS geometry.
- [x] Constraints reject illegal states, duplicate step attempts, invalid progress, and orphan artifacts/events.
- [x] Repository tests prove atomic transition/event writes and task reconstruction after a new process connection.

**Verification:**

- [x] `docker compose run --rm master-agent alembic upgrade head`
- [x] `docker compose run --rm master-agent pytest services/master/tests/integration/test_repository.py -q`

**Dependencies:** T02, T04.

**Files likely touched:** `infra/db/migrations/`, `services/master/src/hennongxi_master/repository.py`, `services/master/tests/integration/`.

**Estimated scope:** M.

#### Checkpoint B: Runtime and source of truth

- [x] T04-T06 verification passes on the actual demo machine.
- [x] Restarting containers retains one reconstructed task and does not expose internal service ports.
- [x] Data provenance/source selection is reviewed before later work treats it as authoritative.

### Phase 3: Events, data preparation, and raster core

#### Task 07: Add the ordered Redis event transport and durable replay abstraction

**Description:** Publish task events to bounded Redis streams only through a Master event-store abstraction that coordinates with durable database events. Implement replay-by-sequence and cache-loss behavior before adding SSE.

**Acceptance criteria:**

- [x] Published events preserve per-task order and all correlation fields, with stable database sequence IDs.
- [x] Replay works after a subscriber disconnect and falls back to PostgreSQL after Redis flush/restart.
- [x] Stream retention is bounded and cache failure never converts a task to `COMPLETED` or loses durable history.

**Verification:**

- [x] `docker compose run --rm master-agent pytest services/master/tests/integration/test_event_store.py -q`
- [x] Redis restart/flush integration case passes.

**Dependencies:** T06.

**Files touched:** `services/master/src/hennongxi_master/events.py`, `services/master/tests/integration/test_event_store.py`, runtime/ADR documentation.

**Estimated scope:** S.

#### Task 08: Deliver Data Agent preparation over HTTP

**Description:** Implement the constrained data-preparation command: validate logical dataset IDs against the approved manifest, inspect the full watershed and all four raster bands, and return normalized/checksummed input metadata without accepting arbitrary paths.

**Acceptance criteria:**

- [x] Valid cached inputs return CRS/grid/bounds/nodata/date/checksum metadata tied to task/step/attempt/correlation IDs.
- [x] Missing, corrupt, mismatched, or under-covering data returns a structured failure and emits no fabricated metadata.
- [x] A network contract test proves Master-style HTTP invocation; no in-process call crosses the service boundary.

**Verification:**

- [x] `docker compose run --rm data-agent pytest services/data_agent/tests -q`
- [x] Internal OpenAPI response validates against `packages/contracts`.

**Dependencies:** T02, T04, T05.

**Files touched:** `services/data_agent/src/hennongxi_data_agent/`, `services/data_agent/tests/`, shared contracts/OpenAPI, backend image and runtime documentation.

**Estimated scope:** M.

#### Task 09: Implement and prove deterministic NDVI/change math

**Description:** Build pure Rasterio/NumPy functions for aligned clipping, NDVI, difference, classification, and projected-area statistics using generated tiny fixtures. Keep I/O and math independent from HTTP orchestration.

**Acceptance criteria:**

- [x] Tests prove NDVI/difference values, nodata and zero-denominator masking, complete-watershed clipping, and grid mismatch rejection.
- [x] Change classes and area totals are deterministic and agree with pixel area within a documented tolerance.
- [x] Outputs retain expected CRS/transform/bounds/nodata and contain no random or precomputed analysis values.

**Verification:**

- [x] 纯栅格逻辑模块的定向分支覆盖命令通过，20 项测试、分支覆盖率 95.72%；HTTP、执行器和
  原子存储由 T10 集成测试负责，不计入 T09 的纯逻辑覆盖分母。
- [x] Critical pure raster logic reaches at least 90% branch coverage.

**Dependencies:** T01, T02; T05 supplies the later real-data check but unit fixtures are generated at test time.

**Files touched:** `services/analysis_agent/src/hennongxi_analysis_agent/`, `services/analysis_agent/tests/unit/`, Analysis Agent dependency metadata, and runtime documentation.

**Estimated scope:** M.

#### Checkpoint C: Trusted inputs and math

- [x] T07-T09 tests pass, including cache loss and raster edge cases.
- [x] A real cached data preflight succeeds, and one small fixture produces inspectable georeferenced outputs.
- [x] No HTTP payload or LLM field can select an arbitrary filesystem path.

### Phase 4: Computed products, quality, and publishing

#### 任务 10：暴露 Analysis Agent 执行接口并原子发布成果

**说明：** 将已验证的栅格核心接入内部 Analysis HTTP 命令，把任务级成果原子写入命名卷，计算面积统计，并返回带校验和、耗时和完整作用域的成果元数据。

**验收标准：**

- [x] 一个 HTTP 请求在正确的 task/attempt 下生成两期 NDVI、差值、变化分级 GeoTIFF 和面积统计 JSON。
- [x] 重复幂等键返回经校验的既有结果；失败或残留的 staging 写入不会发布为完整成果。
- [x] 进度日志携带 task、step、attempt 和 correlation 标识，不记录密钥、请求体或私有文件路径。

**验证：**

- [x] `docker compose run --rm analysis-agent pytest services/analysis_agent/tests -q`
- [x] 真实 Compose 私网 Data→Analysis 测试重新打开每个成果，校验 CRS、bounds、nodata、尺寸、SHA-256 和重复请求复用。
- [x] 最终后端全量测试为 `140 passed, 1 skipped`；需成果卷的真实网络用例另行运行并通过。

**依赖：** T04、T08、T09。

**实际修改文件：** `services/analysis_agent/src/hennongxi_analysis_agent/`、`services/analysis_agent/tests/`、共享契约/OpenAPI、Compose 环境和后端测试镜像。

**规模：** M。

#### 任务 11：交付独立 Quality Agent 评估与原子报告

**说明：** Quality Agent 通过私网 HTTP 接口独立重开 Analysis 的固定成果，核对成果引用、SHA-256、空间网格、像元值域和统计一致性，并计算流域覆盖率、有效像元率、输出完整性及 Analysis 耗时。评估结果包含明确阈值、中文证据与 PASS/FAIL 结论，并原子发布到独立质量报告卷。

**验收标准：**

- [x] 四项指标均来自受控元数据或独立栅格检查；覆盖率取四个栅格覆盖率最小值，有效像元率取四个栅格有效率最小值，完整性要求固定的 5/5 成果，耗时使用非负毫秒整数。
- [x] 任一成果缺失、校验和不符、媒体类型错误、损坏、网格或值域非法、统计不一致、覆盖率或有效像元率不足，都不能得到 PASS。
- [x] 阈值边界、已知好坏夹具、结构化错误、幂等复用、报告篡改和 Data→Analysis→Quality 私网响应契约均有测试覆盖。

**验证：**

- [x] `docker compose run --rm quality-agent pytest services/quality_agent/tests -q`
- [x] 修正后真实 Sentinel-2 数据的私网链路结论为 PASS：流域覆盖率 `1.0000`、有效像元率约 `0.9312`、输出完整性 `5/5`。
- [x] 重复幂等键返回经重新校验的同一结果；质量报告的字节数与 SHA-256 和响应元数据一致。
- [x] 最终后端全量测试为 `168 passed, 3 skipped`；Ruff、格式、Mypy、OpenAPI、Compose 配置和 `uv sync --frozen` 镜像构建全部通过。

**依赖：** T02、T04、T10。

**实际修改文件：** `services/quality_agent/src/hennongxi_quality_agent/`、`services/quality_agent/tests/`、共享契约/OpenAPI、Compose 卷和环境、后端测试镜像及运行文档。

**规模：** M。

#### 任务 12：安全发布栅格瓦片与成果元数据

**说明：** Publisher 只从通过 Analysis/Quality 收据、任务作用域、字节数和 SHA-256
复核的固定成果中读取栅格。Rio-Tiler 将四类成果渲染为 MapLibre 可用的 PNG，内部
`publish` 命令根据批准的 G2 清单和真实栅格生成日期、WGS84 边界、单位、数据归属和
有序图例；任何 HTTP 请求都不能提供本地路径。

**验收标准：**

- [x] 已批准的前后期 NDVI、差值和变化分级均能使用固定色带生成非空 Web Mercator
  瓦片，nodata 像元保持透明。
- [x] 未知成果类型、非法坐标、目录穿越、跨任务访问、收据不一致和质量未通过均返回
  脱敏的结构化 4xx 响应。
- [x] 四个瓦片资源均包含 Web 配置图层所需的成果身份、WGS84 边界、前后日期、单位、
  数据归属和有序颜色图例。

**验证：**

- [x] Publisher、共享契约、Compose 定向回归为 `109 passed, 2 skipped`；Ruff、格式、
  Mypy（20 个源文件）、OpenAPI 和 Compose 配置校验均通过。
- [x] 检查点后端全量回归为 `207 passed, 5 skipped`；仓库级 Ruff、86 个文件格式检查和
  43 个后端源文件 Mypy 均通过。
- [x] 生成夹具验证所有固定色带、非法 XYZ、256×256 PNG、透明度和代表性像素颜色。
- [x] 真实 G2 成果私网测试为 `2 passed`：一项复核公开 PNG，另一项用真实
  Analysis/Quality 收据复核四个资源的日期、边界、单位、归属和图例。

**依赖：** T02、T04、T10，以及已批准的 Publisher 瓦片路由。

**实际修改文件：** `services/publisher_agent/src/hennongxi_publisher_agent/`、Publisher
测试、共享契约/OpenAPI、Compose 配置、开发文档和任务清单。

**规模：** M。

#### 检查点 D：GIS 成果链

- [x] T10-T12 已分别通过 Analysis、Quality、Publisher 三个独立 Agent 容器验证。
- [x] 同一真实 G2 任务已产出计算栅格、独立质量证据、完整图层元数据和可查看瓦片。
- [x] 失败、部分、篡改、跨任务或未通过质量检查的成果既不会标记完整，也不能公开出图。

### Phase 5: Report, LLM planning, and public task entry

#### 任务 13：生成并下载中文 PDF 报告

**说明：** 使用 ReportLab 和随镜像分发的可再分发中文字体生成任务绑定报告。报告需
说明任务与数据日期、计划/Agent 执行、NDVI 变化统计、质量证据、限制和成果校验和。

**验收标准：**

- [x] 完整夹具生成任务绑定 PDF，中文可读、没有缺字方框，并包含全部必需指标与结论。
- [x] 下载路由只提供请求任务已登记的报告，并返回安全文件名和内容响应头。
- [x] 缺失或不完整输入显式失败，不得生成看似成功的报告。

**验证：**

- [x] 已固定 Noto CJK 官方提交 `f8d157532fbfaeda587e826d4cd5b21a49186f7c` 的
  简体中文子集 TTF，记录字体/许可证 SHA-256，并验证关键中文字符和非 editable 镜像安装包。
- [x] `docker compose run --rm publisher-agent pytest services/publisher_agent/tests -q -k report`
- [x] PDF 文本提取与页面渲染图像检查通过；真实 G2 报告为两页 A4，逐页无缺字、截断、重叠或空白孤页。

**依赖：** T11、T12，以及已批准的 Publisher 下载路由。

**预计修改文件：** Publisher 报告生成器、下载路由、字体资产/许可证和报告测试。

**规模：** M。

#### 任务 14：实现安全的大模型规划适配器

**描述：**调用已配置的兼容端点，将 JSON 计划解析为共享模式，强制固定生态监测步骤顺序与
白名单，持久化脱敏调用元数据，并明确标记恢复计划和错误行为。

**验收标准：**

- [x] 假 HTTP 供应商覆盖结构化成功、畸形 JSON、超时、认证、限流和非法步骤映射。
- [x] API Key、Authorization 和不安全模型字段不会进入日志、数据库记录、响应或异常。
- [x] 显式真实冒烟命令完成配置调用，并只记录供应商源指纹、模型、耗时、状态、令牌数和
  响应哈希。

**验证：**

- [x] `docker compose run --rm --no-deps master-agent pytest services/master/tests -q -k llm`
- [x] PostGIS 仓库集成测试证明脱敏失败元数据与恢复计划同一事务落库，非法组合不留下部分记录。
- [x] 已提供凭据时，显式真实冒烟返回白名单有效计划；未配置时只报告准确的非敏感阻塞项。

**依赖：**T02、T06。

**预计修改文件：**Master 大模型适配器、规划模块、真实冒烟入口及假供应商测试。

**规模：**M。

#### 任务 15：暴露任务创建、查询、健康检查和就绪 API

**说明：**实现 Master 的非流式公共 API，包括输入校验、返回 `202` 的任务创建、持久化任务重建、Agent 聚合健康检查，以及不暴露密钥的配置就绪检查。创建请求只提交可由数据库工作者认领的任务，不等待分析完成。

**验收标准：**

- [x] 合法中文输入返回唯一的 `task_id` 和 `PENDING`；非法、空白或超长输入返回契约规定的结构化错误。
- [x] 全新 Master 进程可按已批准的共享契约一致重建任务、attempt、plan、steps、progress、artifacts 和 `last_error`；事件摘要与实时事件流由任务 17 实现。
- [x] 健康/就绪响应清晰区分服务连通性、LLM 配置、已批准数据清单、数据库和 Redis，且不暴露密钥或连接详情。

**验证：**

- [x] 容器内任务 API、健康/就绪、批准数据自动登记及 PostGIS 重启恢复测试通过（19 项）。
- [x] OpenAPI 生成、规范校验和运行时契约比较通过；完整后端回归测试通过（261 项通过，16 项按宿主机环境跳过）。

**依赖：**T03、T05-T07、T14。

**实际修改文件：**Master 任务 API、数据仓库与批准流域加载器，观测应用生命周期，共享 OpenAPI 生成器、Compose 配置及对应测试。

**规模：**M。

#### 检查点 E：公共后端接口

- [x] T13-T15 验证通过，所有公共响应与 OpenAPI 一致。
- [x] 任务在处理开始前即可创建和查询，LLM/数据就绪阻塞项如实呈现。
- [x] 固定样例报告可读中文，并且只能通过经过校验的任务归属关系下载。

### 阶段 6：编排、事件流与恢复

#### 任务 16：通过网络编排完整 Agent 链

**说明：**在任务创建请求生命周期之外执行已批准计划，通过 HTTP 依次调用 Data → Analysis → Quality → Publisher，持久化每个合法的任务状态、步骤和事件转换，汇总成果，并且只在全部必需成果存在后完成任务。

**验收标准：**

- [x] 所有 Agent 请求、日志、持久化步骤、事件和成果均可追踪到同一个 `task_id`、尝试次数和关联标识。
- [x] 精确持久化合法状态顺序且进度单调递增；只有质量结论通过并存在全部 7 类必需成果时才可进入 `COMPLETED`。
- [x] 超时、非法 Agent 响应和服务不可达均会在正确步骤如实进入 `FAILED`，并保存结构化错误。

**验证：**

- [x] `docker compose run --rm master-agent pytest services/master/tests/integration/test_orchestration.py -q`
- [x] HTTP 服务边界观察器记录到四个固定私网端点，证明各 Agent 步骤没有使用进程内导入或调用。
- [x] Compose 生命周期冒烟任务 `ab5bc482-3ba8-4e03-89b9-f395d4e5bc9c` 使用真实 LLM 计划完成，进度为 100%，并生成全部 7 类成果。

**依赖：**T08、T10-T15。

**涉及文件：**`services/master/src/hennongxi_master/orchestrator.py`、`agent_client.py`、`worker.py`、`runtime.py`、Master 集成测试和 Compose 配置。

**工作量：**中等。

#### 任务 17：通过 SSE 推送持久化进度并提供轮询降级语义

**说明：**实现支持重放、心跳、断开清理、`Last-Event-ID` 和已记录轮询行为的任务事件端点，确保慢速或断开的客户端不会阻塞任务执行。

**验收标准：**

- [x] 订阅方按顺序收到状态、进度、错误和成果事件，并可使用最后事件编号无重复、无缺口地重连。
- [x] Redis 丢失时触发持久化重放或降级；断开的客户端及时释放资源。
- [x] 查询端点包含足够的当前状态，使 Web 轮询可得到相同的终态结果。

**验证：**

- [x] `docker compose run --rm master-agent pytest services/master/tests/integration/test_sse.py -q`
- [x] 连接后实时发布、慢客户端、重连、非法游标、Redis 丢失和终态关闭测试通过。
- [x] 真实 SSE 长连接在 Redis 实际重启期间连续收到 7 个心跳，断开后没有遗留阻塞的 `XREAD` 客户端。
- [x] 全仓回归为 326 项通过、4 项按环境条件跳过；Ruff、118 个文件的格式检查和 56 个生产源码文件的严格类型检查通过。

**依赖：**T07、T15、T16。

**涉及文件：**Master 事件 API、SSE 适配器、事件存储、OpenAPI 契约、Compose 配置和 SSE 集成测试。

**工作量：**中等。

#### 任务 18：实现安全重试与启动恢复

**说明：**增加失败任务重试、尝试历史、上游校验和验证、下游重置、幂等控制，以及中断的非终态任务在启动时的恢复；此前失败必须保持可见。

**验收标准：**

- [x] 只有 `FAILED` 任务可重试；新尝试从失败后的安全检查点恢复，并保留此前所有事件和成果历史。
- [x] 并发或重复重试请求具备幂等性；上游成果无效时必须重新计算，不能不安全复用。
- [x] 分析期间重启 Master 会产生可恢复或明确失败状态，随后可到达正确终态且不会重复完成步骤。

**验证：**

- [x] `docker compose run --rm master-agent pytest services/master/tests/integration/test_retry_recovery.py -q`
- [x] Data、Analysis、Quality、Publisher 的强制失败均从预期检查点恢复。
- [x] 6 个 PostGIS 重试/恢复集成场景通过；全仓回归为 348 项通过、4 项按环境条件跳过，Ruff、120 个文件的格式检查和 56 个生产源码文件的严格类型检查通过。

**依赖：**T16、T17。

**涉及文件：**Master 恢复模块、重试 API、编排器改动和重试集成测试。

**工作量：**中等。

#### 检查点 F：具备恢复能力的后端流程

- [x] T16-T18 的完整运行、强制失败、重试、刷新和 Master 重启验证通过。
- [x] 不把任何失败路径显示为成功，且尝试历史始终可查询。
- [x] 使用一个关联标识即可重建完整的跨容器工作流。

### 阶段 7：中文可观测 Web 体验

#### 任务 19：构建可访问的地图优先外壳、任务输入和就绪面板

**说明：**使用严格 TypeScript 的 React/Vite 应用替换 Web 占位页，提供醒目的地图画布、中文任务输入、就绪与 Agent 状态、响应式布局、无障碍控件以及带类型的 Master 客户端。

**验收标准：**

- [x] 用户可以查看配置与 Agent 就绪状态，并且只提交一次有效中文请求；界面具备加载和结构化错误状态。
- [x] 布局可在目标笔记本和窄视口使用，具备键盘焦点、可访问标签、合格对比度和减少动态效果的适配。
- [x] API 类型由已批准的模式生成或校验；传输层的 `snake_case` 不会以临时字段名泄漏到组件中。

**验证：**

- [x] `docker compose run --rm web npm test -- --run -t 'task submission|readiness'`
- [x] `docker compose run --rm web npm run lint && docker compose run --rm web npm run typecheck`
- [x] Web 共 14 项测试通过；lint、类型检查、构建和 OpenAPI 生成类型一致性检查均通过。
- [x] 在 1280×720 与 390×844 的真实浏览器中完成运行检查，无横向溢出、控制台错误或警告；键盘焦点、文本对比度和减少动态效果适配符合要求。
- [x] 通过真实 Compose 环境确认 Master、4 个 Agent、PostGIS 和 Redis 共 7 项服务正常，任务按钮仅在系统就绪时启用。

**依赖：**T01、T02、T15。

**实际修改文件：**`apps/web/src/app/`、`apps/web/src/api/`、`apps/web/src/components/`、Web 测试与样式、严格 TypeScript/Vite/Vitest/ESLint 配置及生成的 OpenAPI 类型。

**工作量：**中等。

#### 任务 20：使用 SSE 与轮询恢复渲染实时 Agent 时间线

**说明：**增加任务级时间线：建立 SSE 连接，以中文呈现每个 Agent、步骤、状态、进度、时间和错误；断线后回退到有界轮询，并在页面刷新后重建状态。

**验收标准：**

- [x] 使用同一个可见的 `task_id` 关联有序 Agent 步骤、进度、耗时、当前状态和真实失败详情。
- [x] SSE 断开后使用退避策略切换到轮询，并能恢复流式连接且不产生重复时间线事件。
- [x] 刷新后打开现有任务地址，可以重建计划与时间线并继续到正确的终态。

**验证：**

- [x] `docker compose run --rm --no-deps web npm test -- --run -t 'timeline|SSE|polling|refresh'`
- [x] 精确测试覆盖分块 SSE、`Last-Event-ID`、事件去重、1/2/4/8 秒有界退避、轮询降级、流式恢复、卸载清理、URL 刷新和非法任务地址；最终 Web 共 30 项测试通过。
- [x] 真实 Compose 任务 `2b7d6183-d273-4b1f-9d0b-9ab0fa6c74b5` 使用真实大模型计划并完成四个私有 Agent 步骤；刷新相同任务地址后重建 5 个唯一阶段和 100% 终态。
- [x] 在 1280×720 与 390×844 的真实浏览器中确认任务编号完整显示、无横向溢出、无重复阶段、无控制台错误或警告。
- [x] 短暂停止 Master 时页面进入“轮询恢复”，服务恢复后自动回到 5 个阶段的完成态；终态 SSE 正常关闭且没有遗留连接。
- [x] 最终五轴审查修复了完整超大 SSE 帧绕过 262,144 字符缓冲限制的问题，并加入回归测试。

**依赖：**T17、T19。

**实际修改文件：**`apps/web/src/features/timeline/`、`apps/web/src/api/client.ts`、`apps/web/src/api/sse.ts`、`apps/web/src/api/task-contract.ts`、任务 URL 状态、组件/应用测试与时间线样式。

**实施提交：**`581994a`、`71cf057`、`b36918f`、`28e29c6`、`6366123`、`0e0c29c`。

**工作量：**中等。

#### 任务 21：使用 MapLibre 显示流域与计算栅格图层

**说明：**渲染完整神农溪流域边界，并提供前期 NDVI、后期 NDVI 和差值瓦片切换；同时展示日期、自动适配边界、图例、加载/错误状态和稳定的图层顺序。

**契约补充：**Publisher 已验证的日期、边界、单位、数据归属、图例和安全相对资源地址保存在当前尝试的发布步骤输出中。现有 `GET /api/v1/tasks/{task_id}` 响应增加可空的 `publication` 字段来重建该输出；不新增公共路由，既有消费者可继续忽略该字段。Web 使用已批准且离线打包的完整流域边界，不在演示时下载边界数据。

**验收标准：**

- [x] 地图初始适配完整流域范围；三个计算图层均可独立激活，流域边界始终可见。
- [x] 图例、日期、数值/单位、颜色、nodata 透明度和数据归属与 Publisher 元数据一致。
- [x] 瓦片缺失或失败时显示可恢复的中文错误，不导致其余工作流界面崩溃或空白。

**验证：**

- [x] Web 全量测试 35 项、TypeScript、ESLint 与生产构建通过；地图模型、组件和完整边界均有专项测试。
- [x] 使用真实完成任务 `2b7d6183-d273-4b1f-9d0b-9ab0fa6c74b5` 验证：差值与后期瓦片请求均返回 200，桌面及 390×844 视口视觉正常，浏览器控制台无警告或错误。

**依赖：**T12、T19、T20。

**实际修改文件：**`apps/web/src/features/map/`、`apps/web/src/components/MapWorkspace.tsx`、内置完整流域 GeoJSON、地图/应用样式与测试、任务查询/OpenAPI 契约，以及 Master 当前发布输出重建。

**实施提交：**`d8ec9ee`、`1ba8401`、`7575b3f`、`a6f5368`。

**工作量：**中等。

#### Checkpoint G: Observable user journey

- [x] T19-T21 测试、Lint 和类型检查通过，刷新与 SSE 中断后界面仍可使用。
- [x] 无需阅读代码即可识别每个 Agent、共享任务编号、当前进度和三个真实计算图层。
- [x] 目标机器浏览器控制台干净，必需的任务与瓦片网络请求均成功。

### Phase 8: Results and end-to-end proof

#### 任务 22：展示质量、统计、报告下载与重试控制

**说明：**完成结果体验，展示双时相日期、NDVI 变化面积统计、四项质量指标与结论、
任务绑定报告、可见的大模型调用证据，并为失败任务提供上下文明确的安全重试入口。

**契约补充：**现有 `GET /api/v1/tasks/{task_id}` 响应增加当前尝试的可空
`analysis` 与 `quality` 字段；Master 只从已校验的当前尝试步骤输出重建它们，Web 再次
严格校验任务、尝试次数、关联标识、成果完整性和报告相对路径。重试继续使用既有
`POST /api/v1/tasks/{task_id}/retry`，不新增公开路由。

**验收标准：**

- [x] 完成任务显示前后日期、增稳减与有效面积（公顷）、覆盖率、有效像元率、输出
  完整性、分析耗时、质量结论和中文 PDF 下载。
- [x] 界面明确区分真实大模型规划与内置恢复计划，失败或未完成任务不会显示“通过”或
  报告下载入口。
- [x] 失败任务显示责任 Agent、步骤与结构化错误；仅可重试错误显示操作，提交期间锁定
  重复请求，接受后跟随新尝试并保留旧失败证据。

**验证：**

- [x] 专项命令
  `docker compose run --rm --no-deps web npm test -- --run -t 'results|quality|report|retry'`
  通过 13 项；Web 全量 46 项、ESLint、TypeScript、生产构建和 OpenAPI 类型同步通过。
- [x] 后端共享契约 59 项、OpenAPI、Ruff、110 个文件格式检查和 57 个源码文件 Mypy
  通过；此前 T22 仓储/API 定向回归分别通过 14 项与 61 项。
- [x] 真实任务 `b38520aa-beae-4446-a4a2-528cf0c045a4` 第 1 次执行在强制停止
  Publisher 后于 80% 如实失败；恢复服务并从界面重试后，第 2 次只复用已校验的
  Data、Analysis、Quality 检查点并完成 Publisher，时间线没有被旧尝试终态截断。
- [x] 同一任务展示真实模型 `claude-sonnet-4-6`、四项质量通过、2019-08-19 至
  2024-08-12 的面积统计；桌面与 390×844 真实浏览器布局无溢出，控制台警告/错误为 0，
  任务、瓦片与报告请求均成功。
- [x] 下载报告为 2 页 A4、61,412 字节，文本与元数据均包含相同任务 ID 和第 2 次尝试；
  `Content-Type`、安全附件文件名、`ETag` 与 SHA-256
  `5ee2b1f71ab17dc1ed1c02bd10ea6ac415686916641aa3eda982b70a31f515e6` 一致，逐页
  渲染无缺字、截断、重叠或空白孤页。
- [x] 五轴代码审查未发现剩余阻断项，并修正了成果面板加入后“成果证据”和“运行环境”
  重复使用章节号 03 的答辩可读性问题。

**依赖：**T13、T14、T18、T20。

**实际修改文件：**`apps/web/src/features/results/`、任务查询/重试客户端与严格契约解析、
时间线跨尝试游标恢复、应用与结果样式/测试、共享任务契约/OpenAPI，以及 Master 当前尝试
结果重建。

**实施提交：**`9d82884`、`94375bb`、`1af2006`、`39c1ca6`、`06d025b`。

**工作量：**中等。

#### 检查点 G2：结果与恢复体验

- [x] T21-T22 的测试、Lint、类型、构建和契约检查通过，真实浏览器控制台与必需网络
  请求干净。
- [x] 无需阅读代码即可理解任务、Agent 链、地图、质量、失败、重试和任务绑定报告。

### 阶段 9：高德位置校验与全链证明

#### 任务 23：建立安全、可降级的高德 Web 服务适配器

**说明：**在 Master 内建立后端专用的高德 Web 服务客户端和配置边界。只调用固定
`https://restapi.amap.com` 的地理编码/POI 搜索能力，严格解析 `status`、`info`、
`infocode` 和最小匹配字段；本任务先证明适配器，不改变任务状态机或公开路由。

**验收标准：**

- [x] `AMAP_WEB_SERVICE_KEY` 仅注入 Master；未配置时返回明确的可选能力状态，不把 Key、
  完整查询串或原始响应写入日志、数据库和错误响应。
- [x] 客户端固定 HTTPS 目标、限制连接/读取超时和最大响应体，拒绝重定向、非 JSON、缺失
  必需字段与自相矛盾的响应；允许忽略有界的新增字段，并把 `10000`、密钥错误、无权限、
  日限额、频率限制和服务繁忙映射为提供商无关结果。
- [x] 生产代码不缓存高德数据；测试只使用本地假服务和自有夹具，显式真实冒烟只输出脱敏
  状态与匹配数量。

**验证：**

- [x] 高德适配器与冒烟测试通过 70 项，Master 全量测试通过 179 项；两者均在最新构建的
  `master-agent` 镜像中执行。
- [x] 假服务覆盖成功、零结果、多结果、认证、限流、超时、重定向、压缩/超大响应、畸形 JSON 和
  密钥脱敏；显式真实冒烟返回 `infocode=10000`，且 Git/日志中没有密钥。
- [x] Compose 拓扑测试通过 9 项，默认配置与本地高德配置均通过
  `docker compose config --quiet`；Ruff、38 个 Master 文件格式检查和 33 个源码文件 Mypy
  通过。
- [x] 安全审查修正压缩响应可能在计数前放大的问题：请求强制 `Accept-Encoding: identity`，
  压缩响应及声明超限响应在读取正文前拒绝；五轴复核未发现剩余阻断项。
- [x] 安全修正后的最新镜像真实调用得到 `VERIFIED`、唯一匹配、`infocode=10000`，耗时
  380 ms；输出仅含固定源指纹、脱敏状态、时间、耗时、可重试标志和匹配数。

**依赖：**T01、T03、T14；门禁 G5。

**实际修改文件：**Master 高德适配器与脱敏冒烟命令及测试、`.env.example`、
`docker-compose.yml`、`docs/development.md` 和 Compose 拓扑测试；未修改任务状态机、公开路由、
数据库、Redis、遥感数据、成果或前端。

**实施提交：**`bab1bcf`、`c20ccf8`、`376949a`、`3e9fe3f`、`5ba13e3`、`cb24bd3`。

**工作量：**中等；核心生产代码与运行配置控制在 5 个文件范围内，其余为测试和文档。

#### 任务 24：把研究区地名校验接入 Master 规划流程

**说明：**在 LLM 规划前先用本地允许别名识别规范内的神农溪任务，再用 T23 的在线适配器
对巴东县范围进行可选交叉校验。只向高德发送固定规范名称与 `adcode`；系统仅持久化本系统
生成的 `VERIFIED`、`DEGRADED`、`REJECTED` 结论、检查时间、耗时和脱敏原因码；Data Agent
仍只接收批准的数据集逻辑标识。

**验收标准：**

- [x] 神农溪/巴东县规范内查询在匹配成功时产生可追踪的“在线位置校验通过”事件，随后仍使用
  G2 流域和影像；高德结果不能改变边界、栅格、日期或计算参数。
- [x] 明确指向其他地区的请求在创建计算任务前收到中文范围错误，不再被静默绑定到神农溪；
  模糊查询不得伪装成已验证。
- [x] 未配置、超时、限流或高德故障时，规范内查询按 G2 本地权威数据继续，并产生“在线位置
  校验已降级”证据；既有公开路由、状态序列和重试语义保持不变。

**验证：**

- [x] Master 规划/编排定向测试覆盖验证、拒绝、降级、重试和重启重建，且旧任务响应仍通过
  OpenAPI/共享契约校验。
- [x] 真实 Compose 中分别完成一次高德可用和一次断网/限流降级任务；两者的遥感成果一致，
  但位置校验证据如实不同。

**依赖：**T05、T15-T18、T23。

**预计修改文件：**Master 研究区解析/规划/编排模块及其测试；不新增公共路由和数据库迁移。

**完成证据（2026-07-21）：**本地允许别名在任务创建前拒绝明确外地区域；规划前在线校验把
`VERIFIED`、`DEGRADED`、`REJECTED`、检查时间、耗时和脱敏原因码写入既有任务事件。
重试会重新校验，重启后的新仓储实例能重建相同任务与事件；Data Agent 仍只收到五个 G2
逻辑数据 ID。显式真实高德 Compose 全链通过，限流降级全链也完成；两次运行的面积统计、
四个确定性栅格和质量门禁一致，任务身份与实测耗时按运行分别记录。

**实际修改文件：**Master 研究区解析、任务创建门禁、编排与运行时模块及单元/Compose 集成
测试；未修改共享公开模型、OpenAPI、任务状态枚举、数据库迁移、四个私有 Agent 或 Web。

**实施提交：**`d8c6aae`、`9ea06e8`、`19bf7ab`、`c510964`、`28c89c0`、`7d38c23`、
`559ac7e`。

**验证结果：**不依赖 PostGIS 的全仓回归 424 项通过、5 项按条件跳过；OpenAPI、Lint、格式、
类型和 32 项契约/Compose 拓扑定向检查通过。真实 Compose 的固定链与在线/限流双路径通过，
显式真实高德全链 1 项通过；浏览器文件、容器日志、PostGIS、Redis、报告/成果卷和 Git
历史的无回显留存扫描均无 Key 或高德原始响应。

**工作量：**中等。

#### 检查点 H1：位置校验边界

- [x] T23-T24 的单元、契约、类型和 Compose 定向验证通过。
- [x] 高德 Key 只存在于忽略的本地环境文件和 Master 进程，浏览器、日志、PostGIS、Redis、
  报告与 Git 均不包含密钥或高德原始响应。
- [x] 同一 G2 数据在高德可用与降级两种情况下产生相同的确定性分析/质量门禁；位置证据如实
  不同，任务身份和实测耗时仍按各次运行独立记录。

#### 任务 25：证明契约和确定性完整 Agent 链

**说明：**增加跨服务契约测试和小型真实栅格夹具，以本地假 LLM 与假高德服务，经 Master
运行 Data → Analysis → Quality → Publisher，断言持久状态、事件、成果、空间输出和报告内容。

**验收标准：**

- [x] 所有内部/公开请求与响应都通过同一套共享版本化 Schema/OpenAPI，没有重复且漂移的模型。
- [x] 高德验证和降级两条确定性链都到达预期终态；完成链的栅格、统计、质量和报告结果在同一
  `task_id` 下数学可复现。
- [x] 损坏数据、非法 LLM 计划、非目标地区、不可达 Agent 和部分成果均进入规定失败/拒绝状态，
  不产生虚假成功。

**验证：**

- [x] 隔离项目构建当前镜像、迁移独立 PostGIS 后执行 `pytest tests/integration -q`，8 项通过。
- [x] 后端单元、契约、集成测试与关键分支覆盖门槛一起通过。

**依赖：**T08、T10-T18、T24。

**预计修改文件：**`tests/integration/`、共享夹具工厂、假 LLM/高德服务。

**工作量：**中等。

**实际结果：**新增共享 4×4 米制真实 GeoTIFF/完整流域夹具和无网络进程内 HTTP 路由器；假
LLM/高德经 Master Worker、真实 PostGIS 和四个真实 FastAPI Agent 边界完成同一固定任务身份
的验证/降级重放。五个分析成果 SHA-256 完全一致，面积分别为增加 0.04、稳定 0.08、减少
0.04、有效 0.16 公顷，质量覆盖率与有效像元率均为 100%，PDF 数学正文一致。非法 LLM 计划
只以 `BUILTIN_RECOVERY` 完成且持久化 `FAILED/LLM_PLAN_INVALID`；其余四类失败均未发布 PDF。

**实际修改文件：**`tests/fixtures/deterministic_gis.py`、`tests/integration/`、
`.gitignore`、`docs/development.md`、`tasks/plan.md`、`tasks/todo.md`。

**验证结果：**T25 隔离 Compose 测试 8 项通过；无数据库本地后端回归 426 项通过、48 项按
环境条件跳过；Ruff、122 个文件的格式检查、60 个生产源码文件的严格类型检查和 OpenAPI
验证通过；纯栅格逻辑 20 项通过，分支覆盖率 95.72%。本地直接执行全仓测试时，迁移测试会因
缺少 `DATABASE_URL` 失败，因此数据库测试只按文档在隔离 Compose 项目中执行。

#### 任务 26：自动化关键 Compose 浏览器旅程

**说明：**使用 Playwright 对完整 Compose 栈验证就绪、任务创建、位置校验证据、计划/时间线、
地图图层、指标、报告、刷新、强制失败和重试；选择器稳定，测试数据确定。

**验收标准：**

- [ ] 一个命令可构建/启动完整栈并运行中文答辩主旅程，无需人工后端干预。
- [ ] 浏览器断言完整流域、三个计算图层、必需指标、匹配报告/任务 ID、刷新恢复、失败/重试，
  以及高德“验证通过/已降级”两种真实文案。
- [ ] 失败截图、trace 和日志可供诊断；成功生成物保持 Git 忽略，测试不访问真实高德服务。

**验证：**

- [ ] `docker compose run --rm e2e npm test`
- [ ] 在目标 Apple Silicon 机器上通过全新 volume 和温缓存复跑。

**依赖：**T04-T05、T19-T22、T25；门禁 G4。

**预计修改文件：**`tests/e2e/`、E2E 包/配置、Compose E2E 服务。

**工作量：**中等。

#### 检查点 H2：可接受用户流程

- [ ] T22-T26 的验证可从仓库根目录以文档命令运行。
- [ ] 规格的 12 项成功标准均有自动化证据或具名人工演练检查。
- [ ] 新鲜与温缓存运行均不把密钥、原始数据、高德响应或生成成果加入 Git。

### 阶段 10：加固与交付

#### 任务 27：执行安全、可观测性和运行加固

**说明：**审查完整实现的输入/路径/密钥、外部服务 SSRF、跨任务成果访问、超时/资源限制、
关联信息、健康语义、高德条款边界和故障诊断；只修复 MVP 阻断项。

**验收标准：**

- [ ] 负向测试覆盖任务输入、LLM/高德输出、内部载荷、成果/瓦片路径、跨任务访问、超时、
  重定向、响应上限和密钥脱敏。
- [ ] 结构化日志可跨容器追踪一个任务/尝试，但不含凭据、敏感原始提示、私有数据 URL 或
  高德完整请求/响应。
- [ ] 健康/就绪、容器限制和错误消息可区分用户错误、必需依赖故障与高德可选增强降级；人工
  复核确认没有抓取、缓存、镜像或持久化高德相关内容。

**验证：**

- [ ] 加固后全量 lint、类型、单元、契约、集成和浏览器测试通过。
- [ ] 人工密钥扫描、跨任务/目录穿越测试和高德数据留存检查均无泄漏或越权。

**依赖：**T26。

**预计修改文件：**审查发现的聚焦服务/Web 模块、安全测试和运行文档；无关发现分别提交。

**工作量：**中等。

#### 任务 28：演练真实答辩并完成交付文档

**说明：**在目标机器使用批准的缓存真实数据、真实 LLM 和显式高德冒烟完成一次记录化全栈
演练，再关闭高德网络完成一次降级复跑；记录配置、检查、演示、限制、恢复和非破坏性停止。

**验收标准：**

- [ ] 一次真实 LLM/真实栅格 UI 运行满足 12 项成功标准，并记录任务 ID、耗时、校验和、
  截图/日志引用、位置校验状态和中文报告。
- [ ] 页面刷新与强制失败/重试成功；关闭高德网络后规范内任务仍可用缓存数据完成，答辩时
  不需要下载影像，也不把高德作为必需依赖。
- [ ] README/运维/演示文档让新操作员能安全配置、预检、启动、验证、演示、停止和排障，
  并明确 Key 轮换、高德额度/状态码和 GCJ-02/WGS84 限制。

**验证：**

- [ ] 在目标机器执行 `docs/demo-runbook.md` 全部命令，包括冷启动、就绪、冒烟/E2E、真实
  工作流、高德降级、重试和非破坏性停止。
- [ ] `git status --short` 只含预期源码/文档；原始数据、`.env*`、高德响应、输出、报告和
  日志保持忽略。

**依赖：**T05、T14、T27；需要有效 LLM 配置、可轮换的高德 Web 服务 Key 和运行中的容器后端。

**预计修改文件：**`README.md`、`docs/setup.md`、`docs/demo-runbook.md`、
`docs/verification.md`。

**工作量：**中等。

#### 最终检查点：可评审、可答辩

- [ ] 规格验收标准和本计划 Definition of Done 全部满足。
- [ ] `docker compose up --build` 与所有文档化一键检查在目标机器通过。
- [ ] 真实演练证据已复核，回滚/重试/高德降级说明明确，演示不依赖外部影像下载。

## 可并行机会

- T23 的假服务/客户端测试可与 T25 的确定性栅格夹具准备并行，但 T25 的完整链必须等待 T24。
- T26 的 Playwright 基础配置可提前准备，但高德位置文案断言必须等待 T24 契约稳定。
- T27 必须在 T26 之后做最终审查；共享契约、状态机、路由和成果元数据不得并行重复定义。

## 风险与缓解

| 风险 | 影响 | 缓解措施 / 最早验证任务 |
| --- | --- | --- |
| 高德服务超时、限流或现场无网 | 高 | T23 严格超时/状态码，T24 可观测降级，T28 断网复跑；不设为核心就绪阻塞项 |
| 高德 Key 泄漏到浏览器、日志或 Git | 高 | Key 仅注入 Master；T23 脱敏测试、T27 密钥扫描、忽略 `.env*` 并在交付前轮换 |
| GCJ-02 与 WGS84 混用导致错位 | 高 | 不替换 MapLibre、不直接叠加高德坐标；G2 几何与分析契约不变，T27 人工复核 |
| 高德数据留存或展示超出官方授权 | 高 | 不缓存/持久化/下载原始响应、边界、POI 或底图；只保存本系统的脱敏结论，T27 条款边界检查 |
| 地名多结果导致错误匹配 | 中 | 本地允许别名先行，POI 搜索限定巴东县 `adcode`，多结果不伪装为验证通过，T24 覆盖歧义测试 |
| 精确 LLM 提供商/模型变化 | 高 | T14 的兼容 HTTP 适配器与假服务矩阵；T28 必须真实调用 |
| 栅格网格/CRS 不同导致错误变化 | 高 | T09 显式对齐契约、T11 独立范围/覆盖检查；高德不参与分析输入 |
| SSE 断线后缺口或重复 | 中 | T07/T17 持久序列与 Redis 丢失测试，T20 轮询降级，T26 浏览器复核 |
| 部分成果被展示为成功 | 高 | T10/T11/T16 原子成果与完成门槛，T25 失败矩阵 |
| 重试重复计算或隐藏历史 | 高 | T18 不可变尝试、安全检查点与校验和；T26 强制失败/重试 |
| 十天计划因新增接入延期 | 高 | T23/T24 各自保持中等以下；不加入高德 JS API、底图、路线、天气或生活 POI；G5 未通过时不实施 |

## 审批事项与外部阻塞项

1. **G1 已批准：**Publisher 两个只读瓦片/下载路由；新增公开路由仍需单独审批。
2. **G2 已批准：**权威流域、2019-08-19 与 2024-08-12 影像及反射率修正；高德不得替代这些来源。
3. **G3 已满足：**大模型配置仅用于后端真实冒烟与演练，不进入 Git 或浏览器。
4. **G4 已满足：**容器后端在 Compose/浏览器验证前运行。
5. **G5 已批准：**按 `tasks/approvals/G5-amap-web-service-integration.md` 接入后端专用、可降级的
   高德研究区校验；不新增公开路由、不替换 MapLibre、不缓存高德数据、不扩展第二 GIS 场景。

不得通过静默扩大范围来处理其他开放问题。若要增加第二个 GIS 场景、高德 JS API/底图、
提供商专用 SDK、新基础设施服务、数据库迁移或公开工作流路由，必须停止实施并请求审批。
