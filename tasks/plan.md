# Implementation Plan: 神农溪分布式多 Agent GIS 演示系统

## Plan status

- Baseline: approved `docs/spec.md` at commit `92e50a6` (`docs: define the approved MVP specification`).
- Repository state at planning time: greenfield; only `.gitignore` and `docs/spec.md` are tracked.
- Scope of this document: the 10-day MVP only. It does not reopen requirements discovery or add scenarios outside ecological change monitoring.
- Approval status: approved by the user on 2026-07-19, including the proposed Publisher resource routes below.

## Overview

Build a Chinese, map-first single-page application in which a user submits a natural-language ecological monitoring request, a real configured LLM produces a schema-validated fixed plan, and five independently deployed Agent services execute a traceable NDVI workflow under one `task_id`. PostgreSQL/PostGIS is the durable source of record, Redis carries ordered progress events, the Master Agent owns public workflow APIs and orchestration, and the Publisher Agent serves computed map products and a Chinese PDF. The implementation is organized to fail fast on the highest-risk areas—Apple Silicon containers, authoritative local GIS data, raster math, cross-service contracts, and real LLM compatibility—while leaving the repository runnable at each checkpoint.

## Planning assumptions

1. `docs/spec.md` is the authoritative product and engineering contract; later code must not silently weaken its success criteria.
2. The LLM provider, base URL, and model may remain unset during most development. Fake-server tests are mandatory, while a real smoke test is an explicit externally gated checkpoint before acceptance.
3. Raw demo imagery is cached locally and remains ignored by Git. Git stores the full watershed boundary, source metadata, acquisition dates, band mapping, checksums, and setup instructions.
4. PostgreSQL is the durable source of truth for tasks, attempts, steps, events, and artifact metadata. Redis is an event/cache transport and may be rebuilt without losing the final task record.
5. The Master starts work outside the request lifecycle and calls every Agent through versioned HTTP endpoints. A single lifespan worker atomically claims runnable rows from PostgreSQL; this durable claim/recovery mechanism avoids adding Celery or another infrastructure service.
6. Generated rasters and reports use a Docker named volume shared only where required. Services exchange commands and metadata through HTTP; no Agent imports another Agent's application code.
7. Full container verification requires the installed OrbStack/Docker-compatible backend to be running. Local unit work may proceed without it, but no container checkpoint can be marked complete without an actual `linux/arm64` run.

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
                  -> T23 contract/integration suite -> T24 Compose/E2E
                      -> T25 hardening -> T26 rehearsal/handoff

T05 authoritative data/cache feeds T08, T09, T23, T24, and T26.
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
| 9 | T23-T25 | Full-chain tests, Compose browser journey, hardening |
| 10 | T26 | Real-LLM/data rehearsal and reproducible handoff |

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

**Dependencies:** T01. Blocks T08, T09, T23, T24, and T26.

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

- [x] `docker compose run --rm analysis-agent pytest services/analysis_agent/tests/unit -q --cov=hennongxi_analysis_agent --cov-branch --cov-fail-under=90`
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

- [ ] 订阅方按顺序收到状态、进度、错误和成果事件，并可使用最后事件编号无重复、无缺口地重连。
- [ ] Redis 丢失时触发持久化重放或降级；断开的客户端及时释放资源。
- [ ] 查询端点包含足够的当前状态，使 Web 轮询可得到相同的终态结果。

**验证：**

- [ ] `docker compose run --rm master-agent pytest services/master/tests/integration/test_sse.py -q`
- [ ] 慢客户端、重连、Redis 重启和终态事件流的并发测试通过。

**依赖：**T07、T15、T16。

**涉及文件：**Master 事件 API、SSE 适配器和 SSE 集成测试。

**工作量：**中等。

#### 任务 18：实现安全重试与启动恢复

**说明：**增加失败任务重试、尝试历史、上游校验和验证、下游重置、幂等控制，以及中断的非终态任务在启动时的恢复；此前失败必须保持可见。

**验收标准：**

- [ ] 只有 `FAILED` 任务可重试；新尝试从失败后的安全检查点恢复，并保留此前所有事件和成果历史。
- [ ] 并发或重复重试请求具备幂等性；上游成果无效时必须重新计算，不能不安全复用。
- [ ] 分析期间重启 Master 会产生可恢复或明确失败状态，随后可到达正确终态且不会重复完成步骤。

**验证：**

- [ ] `docker compose run --rm master-agent pytest services/master/tests/integration/test_retry_recovery.py -q`
- [ ] Data、Analysis、Quality、Publisher 的强制失败均从预期检查点恢复。

**依赖：**T16、T17。

**涉及文件：**Master 恢复模块、重试 API、编排器改动和重试集成测试。

**工作量：**中等。

#### 检查点 F：具备恢复能力的后端流程

- [ ] T16-T18 的完整运行、强制失败、重试、刷新和 Master 重启验证通过。
- [ ] 不把任何失败路径显示为成功，且尝试历史始终可查询。
- [ ] 使用一个关联标识即可重建完整的跨容器工作流。

### Phase 7: Chinese operational Web experience

#### Task 19: Build the accessible map-first shell, task input, and readiness panel

**Description:** Replace the placeholder Web with a strict TypeScript React/Vite application: prominent map canvas, Chinese task composer, readiness/Agent status, responsive layout, accessible controls, and typed Master client.

**Acceptance criteria:**

- [ ] Users can see configuration/Agent readiness and submit a valid Chinese request once, with loading and structured error states.
- [ ] Layout is usable at target laptop and narrow viewport sizes, with keyboard focus, labels, contrast, and reduced-motion behavior.
- [ ] API types derive from or are checked against the approved schema; snake_case transport does not leak ad hoc field names into components.

**Verification:**

- [ ] `docker compose run --rm web npm test -- --run -t 'task submission|readiness'`
- [ ] `docker compose run --rm web npm run lint && docker compose run --rm web npm run typecheck`

**Dependencies:** T01, T02, T15.

**Files likely touched:** `apps/web/src/app/`, `apps/web/src/api/`, `apps/web/src/components/TaskPanel*`, Web tests/styles.

**Estimated scope:** M.

#### Task 20: Render the live Agent timeline with SSE/polling recovery

**Description:** Add a task-scoped timeline that opens SSE, renders each Agent/step/state/progress/time/error in Chinese, falls back to bounded polling, and reconstructs state after page refresh.

**Acceptance criteria:**

- [ ] The same visible `task_id` anchors ordered Agent steps, progress, elapsed time, current status, and honest failure details.
- [ ] SSE disconnect switches to polling with backoff and can resume streaming without duplicated timeline events.
- [ ] Loading an existing task URL after refresh reconstructs the plan/timeline and continues to the correct terminal state.

**Verification:**

- [ ] `docker compose run --rm web npm test -- --run -t 'timeline|SSE|polling|refresh'`
- [ ] Browser runtime check confirms no duplicate events, leaked connections, or console errors.

**Dependencies:** T17, T19.

**Files likely touched:** `apps/web/src/features/timeline/`, streaming hook/API client, timeline tests.

**Estimated scope:** M.

#### Task 21: Display watershed and computed raster layers in MapLibre

**Description:** Render the complete watershed boundary plus toggleable date-1 NDVI, date-2 NDVI, and difference tiles, with dates, bounds fitting, legends, loading/error states, and stable layer ordering.

**Acceptance criteria:**

- [ ] The map initially fits the full watershed and each computed layer can be activated independently without hiding the boundary.
- [ ] Legends, dates, values/units, color ramps, nodata transparency, and source attribution match Publisher metadata.
- [ ] Missing/failed tiles show a recoverable Chinese error and do not crash or blank the rest of the workflow UI.

**Verification:**

- [ ] `docker compose run --rm web npm test -- --run -t 'map|layer|legend'`
- [ ] Real-browser network/visual check confirms expected tile requests, boundary extent, layer switching, and no console errors.

**Dependencies:** T12, T19, T20.

**Files likely touched:** `apps/web/src/features/map/`, map styles/config, map tests.

**Estimated scope:** M.

#### Checkpoint G: Observable user journey

- [ ] T19-T21 tests/lint/types pass and the UI remains usable after refresh and SSE interruption.
- [ ] A teacher can identify every Agent, the shared task ID, current progress, and the three real computed map layers without reading code.
- [ ] Browser console and required network requests are clean on the target machine.

### Phase 8: Results and end-to-end proof

#### Task 22: Present quality, statistics, report download, and retry controls

**Description:** Complete the results experience with both NDVI summaries, change/area statistics, four quality metrics and conclusion, artifact/report links, visible LLM-call evidence, and contextual retry for failures.

**Acceptance criteria:**

- [ ] Completed tasks display dates, NDVI/change statistics, area units/classes, coverage, valid pixels, completeness, elapsed time, quality conclusion, and PDF download.
- [ ] The UI distinguishes real LLM planning from labeled recovery and never renders a failed/incomplete run as complete.
- [ ] Failed tasks show the responsible step/error and a guarded retry action that creates/follows the new attempt.

**Verification:**

- [ ] `docker compose run --rm web npm test -- --run -t 'results|quality|report|retry'`
- [ ] Browser check downloads a valid task-matching PDF and completes one forced retry.

**Dependencies:** T13, T14, T18, T20.

**Files likely touched:** `apps/web/src/features/results/`, retry/report client code, result tests.

**Estimated scope:** M.

#### Task 23: Prove contracts and the deterministic full Agent chain

**Description:** Add cross-service contract tests and a small generated real-raster fixture that runs Data → Analysis → Quality → Publisher through Master with a local fake LLM. Assert durable state, events, artifacts, spatial outputs, and report content.

**Acceptance criteria:**

- [ ] Every internal/public request and response validates against the shared versioned schemas/OpenAPI with no duplicated divergent models.
- [ ] The deterministic chain reaches `COMPLETED` with mathematically expected raster/statistical/quality/report results and one traceable task ID.
- [ ] Representative corrupt data, illegal LLM plan, unreachable Agent, and partial artifact cases end in the documented failure state.

**Verification:**

- [ ] `docker compose run --rm master pytest tests/integration -q`
- [ ] Backend unit/contract/integration suite and critical branch-coverage threshold pass together.

**Dependencies:** T08, T10-T18.

**Files likely touched:** `tests/integration/`, shared fixture factories, fake LLM server.

**Estimated scope:** M.

#### Task 24: Automate the critical Compose browser journey

**Description:** Use Playwright against the complete Compose stack to verify readiness, task creation, plan/timeline progress, map layers, metrics, report download, refresh survival, forced failure, and retry. Keep selectors stable and test data deterministic.

**Acceptance criteria:**

- [ ] One command builds/starts the full stack and runs the critical Chinese demonstration journey without manual backend intervention.
- [ ] The browser test asserts the complete watershed, three computed raster layers, required metrics, matching report/task ID, refresh recovery, and failure/retry.
- [ ] Failure artifacts (screenshots, trace, logs) are retained for diagnosis while successful generated outputs remain ignored by Git.

**Verification:**

- [ ] `docker compose run --rm e2e npm test`
- [ ] Fresh-volume run and warm-cache rerun both pass on the target Apple Silicon machine.

**Dependencies:** T04, T05, T19-T23.

**Files likely touched:** `tests/e2e/`, e2e package/config, Compose e2e service.

**Estimated scope:** M.

#### Checkpoint H: Acceptance flow

- [ ] T22-T24 verification passes from the repository root with documented commands.
- [ ] All 12 specification success criteria have automated evidence or a named manual rehearsal check.
- [ ] Fresh and warm runs leave no secret/raw/generated artifacts staged for Git.

### Phase 9: Hardening and delivery

#### Task 25: Run security, observability, and operational hardening

**Description:** Review the complete implementation for input/path/secret exposure, SSRF-like LLM configuration risks, cross-task artifact access, timeouts/resource limits, correlation coverage, health semantics, and useful failure diagnostics. Fix only MVP blockers found by the review.

**Acceptance criteria:**

- [ ] Negative tests cover task input, LLM output, internal payloads, artifact/tiles paths, cross-task access, timeouts, and secret redaction.
- [ ] Structured logs allow one task/attempt to be traced across all containers without credentials, raw prompts if sensitive, or private data URLs.
- [ ] Health/readiness, container limits/timeouts, and error messages distinguish operator-actionable failures from user errors.

**Verification:**

- [ ] Full lint/type/unit/contract/integration/browser suite passes after hardening.
- [ ] Manual secret scan and cross-task/path traversal test report no leak/access.

**Dependencies:** T24.

**Files likely touched:** focused service/Web modules found by review, security tests, operational docs.

**Estimated scope:** M; split each unrelated finding into its own atomic commit.

#### Task 26: Rehearse the real demonstration and finish handoff documentation

**Description:** On the target machine, use the approved cached real data and configured real LLM for a recorded full-stack rehearsal, then document exact setup, checks, demo script, known limitations, backup/recovery behavior, and teardown without deleting cached data.

**Acceptance criteria:**

- [ ] One real-LLM, real-raster UI run satisfies all 12 success criteria with recorded task ID, timings, checksums, screenshots/log references, and downloadable Chinese report.
- [ ] The run succeeds after page refresh, demonstrates one forced failure/retry, and needs no network imagery download during the presentation.
- [ ] README/operator/demo documentation lets a new operator configure, preflight, start, verify, present, stop, and troubleshoot the system without secret leakage.

**Verification:**

- [ ] Run every command in `docs/demo-runbook.md` on the target machine, including cold start, readiness, smoke/E2E, real workflow, retry, and non-destructive shutdown.
- [ ] `git status --short` contains only intended source/docs changes; raw data, `.env`, outputs, reports, and logs remain ignored.

**Dependencies:** T05, T14, T25; requires supplied working LLM configuration and running container backend.

**Files likely touched:** `README.md`, `docs/setup.md`, `docs/demo-runbook.md`, `docs/verification.md`.

**Estimated scope:** M.

#### Final checkpoint: Ready for review and demonstration

- [ ] All specification acceptance criteria and this plan's Definition of Done are satisfied.
- [ ] `docker compose up --build` and every documented one-command check pass on the target machine.
- [ ] The real rehearsal evidence is reviewed, rollback/retry instructions are known, and no external download is needed during the demonstration.

## Parallelization opportunities

- After T02 freezes contracts, T05 data-source work, T07 event-store work, T09 raster pure logic, and the initial T19 Web shell can proceed independently if changes to shared schemas are coordinated first.
- After T10 publishes stable artifact metadata, T11 quality evaluation and T12 tile rendering can proceed in parallel.
- After T17 fixes event/query semantics, T20 timeline work can proceed while T13 report generation is finalized.
- T23 contract/integration work and T24 E2E scaffolding can be prepared in parallel, but neither checkpoint passes before the complete chain is available.
- Migrations, shared contracts, state transitions, route changes, and artifact metadata changes are sequential coordination points and must not be independently redefined.

## Risks and mitigations

| Risk | Impact | Mitigation / earliest proving task |
| --- | --- | --- |
| Exact LLM provider/model remains unknown | High | Provider-compatible HTTP adapter, fake-server matrix, readiness error, and opt-in real smoke in T14; real call required in T26 |
| Authoritative imagery/boundary unavailable, mismatched, or too large | High | Verify source/license/coverage/checksums and cache procedure in T05 before Agent integration |
| GIS images fail or build slowly on Apple Silicon | High | Pin verified `linux/arm64` bases in T01 and run actual Compose smoke in T04 |
| Raster grids/CRS differ and produce misleading change | High | Explicit reprojection/alignment contract and mismatch tests in T09; independent range/coverage checks in T11 |
| SSE gaps or duplicate progress after reconnect | Medium | Durable monotonic events, `Last-Event-ID`, Redis-loss tests in T07/T17, HTTP polling fallback in T20 |
| In-process shortcuts undermine the distributed claim | High | Independent containers, no cross-Agent imports, service-boundary spies in T08/T16/T23 |
| Partial artifacts look successful | High | Atomic writes, checksum/completeness gates, and no `COMPLETED` until T10/T11/T16 conditions pass |
| Chinese PDF glyph/layout failures | Medium | Bundle licensed font and verify extraction plus rendered pages in T13 |
| Retry duplicates work or hides history | High | Immutable attempts, idempotency, safe checkpoints, checksum validation, and forced-failure tests in T18 |
| Demo depends on network or cold downloads | High | Ignored local cache, preflight, warm run, and recorded offline-data rehearsal in T05/T24/T26 |
| Ten-day schedule slips | High | S/M tasks, checkpoint every three tasks, high-risk work by day 4, and defer all explicitly out-of-scope features |

## 审批事项与外部阻塞项

以下事项不是重新讨论已批准产品范围的理由，而是规格中已经预留的实施门禁：

1. **批准 Publisher 资源契约：**批准本计划提出的两个只读瓦片/下载路由。任何不同或
   新增的公开路由都需要单独审批。
2. **已批准选定的权威数据源：**G2 及其 2024-08-12 后期影像变更已于 2026-07-19
   获批；T05 仍必须记录准确的来源、许可、获取日期、覆盖率和校验和，数据才能被视为
   最终来源。
3. **提供大模型运行配置：**`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 只在 T14
   可选真实冒烟测试和 T26 验收演练中需要；不得提交到 Git，也不得传给浏览器。
4. **启动容器后端：**执行 T04 及后续 Compose 验证前，OrbStack/Docker 必须运行。

不得通过静默扩大范围来处理其他开放问题。若要增加第二个 GIS 场景、提供商专用 SDK、
新增基础设施服务、在审批后重设计模式，或增加公开工作流 API，必须停止实施并请求审批。
