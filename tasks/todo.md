# TODO: 神农溪分布式多 Agent GIS 演示系统

Source of truth: `docs/spec.md` at `92e50a6`. Detailed rationale, file scope, and commands: `tasks/plan.md`.

## How to use this checklist

- Work in task-number order unless `tasks/plan.md` explicitly marks work as parallel-safe.
- Mark a task complete only when its acceptance and verification sub-items pass.
- Stop at every checkpoint for a green repository-wide check and review.
- Keep raw imagery, `.env`, credentials, generated rasters/reports, logs, and test artifacts out of Git.
- Use small atomic commits; do not combine an unrelated refactor with an implementation task.

## 审批门禁

- [x] G1：批准 `tasks/plan.md`，包括拟议的只读 Publisher 瓦片与下载路由。
- [x] G2：批准 T05 选定的准确权威流域和影像来源。
  - 首次来源组合已于 2026-07-19 获批，但 2024-08-22 影像未通过 90% 流域内
    有效像元门槛；2024-08-12 替换方案已于 2026-07-19 获批。
  - 2024-08-12 COG 的 BOA 重复偏移修正已于 2026-07-20 获批；来源、日期和
    质量阈值不变。
- [x] G3：已提供不提交到 Git 的大模型运行配置，并完成脱敏真实冒烟。
- [x] G4：在容器验证前启动 OrbStack/Docker。

## Day 1 — Contract and runnable foundation

- [x] **T01 Pin toolchain and configuration** (depends: none)
  - [x] Record official-source/version/ARM64/license checks and pin backend/frontend dependencies.
  - [x] Add safe `.env.example` and reproducible dependency checks.
  - [x] Run lock/metadata validation, `git diff --check`, and secret-placeholder review.
- [x] **T02 Freeze schemas, transitions, and OpenAPI** (depends: T01)
  - [x] Define versioned plan/task/step/event/artifact/error and internal command schemas.
  - [x] Document public, SSE, internal, and approved Publisher resource contracts.
  - [x] Pass schema, illegal-transition, unsafe-field, and OpenAPI validation tests.
- [x] **T03 Create five Agent shells and structured logging** (depends: T01-T02)
  - [x] Start independent Master/Data/Analysis/Quality/Publisher FastAPI apps on ports 8000-8004.
  - [x] Propagate correlation IDs in JSON logs and outbound HTTP headers.
  - [x] Pass health, lifespan, Ruff, and Python type checks.

### Checkpoint A

- [x] All T01-T03 checks pass from a clean environment.
- [x] Shared schemas and OpenAPI agree; G1 is approved.
- [x] Each Agent starts independently and one request is traceable in logs.

## Day 2 — Runtime, data, and durable state

- [x] **T04 Assemble Compose topology/readiness** (depends: T03; gate: G4)
  - [x] Add required containers, network isolation, health checks, and persistent volumes.
  - [x] Prove only Web/Master/Publisher resources are host-visible and ARM64 builds start.
  - [x] Pass `docker compose config --quiet`, smoke, and restart-persistence checks.
- [x] **T05 Select/cache authoritative data** (depends: T01; gate: G2 before finalization)
  - [x] Record provenance, license, dates, bands, CRS, nodata, size, and checksums.
  - [x] Verify the complete watershed and adequate dual-date red/NIR coverage.
  - [x] Pass manifest, checksum, raster-inspection, and offline-cache preflight checks.
- [x] **T06 Implement PostGIS migrations/repository** (depends: T02, T04)
  - [x] Persist watershed, tasks, attempts, worker claims, steps, ordered events, LLM metadata, and artifacts.
  - [x] Enforce states, progress, uniqueness, referential integrity, and atomic transitions.
  - [x] Migrate a fresh database and pass repository reconstruction tests.

### Checkpoint B

- [x] T04-T06 pass on the target machine.
- [x] Restart preserves durable task state and volumes without exposing internal ports.
- [x] G2 已批准，权威数据可以在 T05 验证通过后供下游使用。

## Day 3 — Events, data preparation, and raster core

- [x] **T07 Add ordered Redis event transport** (depends: T06)
  - [x] Preserve durable monotonic order and all correlation fields.
  - [x] Replay after disconnect and fall back to PostgreSQL after Redis loss.
  - [x] Pass retention, replay, Redis restart/flush, and no-false-success tests.
- [x] **T08 Deliver Data Agent over HTTP** (depends: T02, T04, T05)
  - [x] Resolve allow-listed logical data IDs and return normalized checksum-bearing metadata.
  - [x] Reject missing/corrupt/mismatched/under-covering inputs with structured errors.
  - [x] Pass network contract tests proving no in-process Agent call/path injection.
- [x] **T09 Implement deterministic NDVI/change core** (depends: T01-T02)
  - [x] Prove NDVI, difference, masks, clipping, alignment, classes, and projected areas.
  - [x] Preserve CRS/transform/bounds/nodata and reject grid mismatches.
  - [x] Reach at least 90% branch coverage for critical pure raster logic.

### Checkpoint C

- [x] T07-T09 pass, including cache-loss and raster edge cases.
- [x] Real cached data preflight and generated fixture inspection succeed.
- [x] Arbitrary filesystem paths cannot enter through HTTP or LLM plans.

## Day 4 — Analysis, quality, and tiles

- [x] **T10 暴露 Analysis Agent 并原子发布成果**（依赖：T04、T08-T09）
  - [x] 通过 HTTP 生成两期 NDVI、差值、变化分级和面积统计。
  - [x] 强制任务级原子写入、校验和、幂等复用，并禁止发布部分成果。
  - [x] 通过服务测试、真实私网测试和 GDAL/Rasterio 成果检查。
- [x] **T11 交付独立 Quality Agent 评估与原子报告**（依赖：T02、T04、T10）
  - [x] 独立检查覆盖率、有效像元率、5/5 输出完整性和 Analysis 耗时，并返回阈值与中文证据。
  - [x] 缺失、损坏、篡改、网格或值域非法、统计不一致及阈值不足的成果均不能通过。
  - [x] 通过边界、好坏夹具、幂等、原子报告、HTTP 契约和真实 Data→Analysis→Quality 私网测试。
- [x] **T12 安全发布栅格瓦片与成果元数据**（依赖：T02、T04、T10；门禁：G1）
  - [x] 冻结 WGS84 边界、前后日期、单位、数据归属和有序颜色图例契约，并拒绝不完整或质量未通过的发布输入。
  - [x] 实现与 HTTP 解耦的 Rio-Tiler 渲染核心，固定 NDVI/差值/分级色带、XYZ 边界与 256×256 PNG nodata 透明度。
  - [x] 只读复核 Analysis/Quality 收据、作用域、PASS 结论、字节数和 SHA-256，并用文件指纹安全缓存已验证成果。
  - [x] 通过公共只读路由渲染固定色带、nodata 透明的 NDVI/差值/分级瓦片。
  - [x] 拒绝目录穿越、非法坐标/类型、跨任务成果及未通过质量检查的访问。
  - [x] 通过瓦片响应契约、代表性 PNG 颜色/透明度断言和真实 G2 成果网络验收。
  - [x] 通过内部 publish 命令生成四个包含边界、日期、单位、图例和成果身份的资源元数据。

### 检查点 D

- [x] T10-T12 已分别通过 Analysis、Quality、Publisher 三个独立 Agent 容器验证。
- [x] 同一真实 G2 任务已产出计算栅格、独立质量证据、完整图层元数据和可查看瓦片。
- [x] 部分、失败、篡改、跨任务或未通过质量检查的成果均不能作为完整成果公开出图。

## Day 5 — Report, LLM, and public task entry

- [x] **T13 生成并下载中文 PDF**（依赖：T11-T12；门禁：G1）
  - [x] 固定 Noto Sans SC 官方字体、上游提交、SHA-256 和 SIL OFL 1.1 许可证，并验证关键中文字符与镜像内安装包。
  - [x] 在报告中包含任务、数据日期、计划、统计、质量、限制和校验和。
  - [x] 强制任务绑定的安全下载；输入不完整时必须显式失败。
  - [x] 通过 PDF 文本提取、页面渲染、字形、响应头和访问控制测试。
- [x] **T14 实现安全的大模型适配器**（依赖：T02、T06；真实冒烟门禁：G3）
  - [x] 验证假供应商成功、畸形响应、超时、认证、限流和非法步骤场景。
  - [x] 证明密钥与不安全字段不会进入日志、持久化、响应或执行流程。
  - [x] 通过显式真实冒烟，或如实记录非敏感 readiness 阻塞项。
  - [x] 通过 PostGIS 集成测试证明脱敏失败元数据与恢复计划原子落库，非法组合完全回滚。
- [x] **T15 暴露任务创建、查询、健康检查和就绪 API**（依赖：T03、T05-T07、T14）
  - [x] 合法中文输入返回 `202`、唯一任务 ID 和 `PENDING`；校验失败返回结构化错误。
  - [x] 重启后按已批准契约重建任务、attempt、plan、steps、progress、artifacts 和 `last_error`；事件流留给 T17。
  - [x] OpenAPI、任务 API、聚合健康检查、配置就绪及 PostGIS 重启恢复测试通过。

### 检查点 E

- [x] T13-T15 通过，所有公共响应与 OpenAPI 一致。
- [x] 任务创建不阻塞且可查询，就绪状态如实呈现。
- [x] 中文报告可读，并与所属任务严格绑定。

## 第 6 天——编排、SSE 与重试

- [x] **T16 通过网络编排完整 Agent 链**（依赖：T08、T10-T15）
  - [x] 所有请求、日志、记录和成果使用同一个任务、尝试次数与关联标识。
  - [x] 持久化合法且单调的状态；只有质量通过并具备全部必需成果时才完成。
  - [x] 完整链及 Agent 超时、非法响应、服务不可达的强制失败测试均通过。
- [x] **T17 通过 SSE 推送持久化进度**（依赖：T07、T15-T16）
  - [x] 实现有序重放、心跳、清理、`Last-Event-ID` 和持久化降级。
  - [x] 确保慢速或断开的客户端不阻塞任务，查询端点支持等价轮询。
  - [x] 通过重连、重复或缺口、Redis 丢失、慢客户端和终态事件流测试。
- [x] **T18 实现安全重试与启动恢复**（依赖：T16-T17）
  - [x] 保留不可变的尝试历史，只从校验和有效的安全检查点恢复。
  - [x] 使重复或并发重试具备幂等性，且重启恢复不会重复执行已完成步骤。
  - [x] 通过每个 Agent 的强制失败/重试以及 Master 重启测试。

### 检查点 F

- [x] T16-T18 的完成、失败、重试、刷新和 Master 重启验证通过。
- [x] 不把失败显示为成功，且此前尝试始终可查询。
- [x] 使用一个关联标识即可重建完整的跨容器工作流。

## Day 7 — Task and timeline Web UI

- [ ] **T19 Build map-first shell/task/readiness UI** (depends: T01-T02, T15)
  - [ ] Provide accessible responsive Chinese readiness, task input, loading, and error states.
  - [ ] Use contract-checked API types and prevent duplicate submission.
  - [ ] Pass component tests, lint, type check, keyboard/focus/contrast checks.
- [ ] **T20 Render live Agent timeline** (depends: T17, T19)
  - [ ] Show task ID, ordered Agents/steps/status/progress/time/error in Chinese.
  - [ ] Recover SSE with polling/backoff and rebuild the task after page refresh.
  - [ ] Pass timeline/SSE/polling/refresh tests and clean-browser runtime check.

### Checkpoint G1

- [ ] T19-T20 pass at laptop and narrow viewport sizes.
- [ ] Task creation through live progress survives disconnect and refresh.

## Day 8 — Map and result UI

- [ ] **T21 Display watershed/computed layers** (depends: T12, T19-T20)
  - [ ] Fit the complete watershed and toggle both NDVI dates/difference while keeping boundary visible.
  - [ ] Match Publisher dates/bounds/legends/colors/units/attribution and handle tile errors.
  - [ ] Pass component plus real-browser tile/network/console/visual checks.
- [ ] **T22 Present results/report/retry** (depends: T13-T14, T18, T20)
  - [ ] Show NDVI/change/area stats, four quality metrics, conclusion, LLM evidence, and PDF.
  - [ ] Distinguish real/fallback planning and failure/incomplete/completed states honestly.
  - [ ] Pass result/report download and forced-retry browser checks.

### Checkpoint G2

- [ ] T21-T22 tests/lint/types pass with clean browser console/network behavior.
- [ ] A teacher can understand the task, Agent chain, maps, quality, failure, retry, and report without code.

## Day 9 — End-to-end proof and hardening

- [ ] **T23 Prove contracts/full deterministic chain** (depends: T08, T10-T18)
  - [ ] Validate every public/internal payload against the single shared contract.
  - [ ] Assert expected raster/statistical/quality/report results under one traceable task ID.
  - [ ] Pass illegal-plan, corrupt-data, unavailable-Agent, and partial-artifact failure cases.
- [ ] **T24 Automate Compose browser journey** (depends: T04-T05, T19-T23; gate: G4)
  - [ ] One command verifies readiness through completion/map/metrics/report/refresh/failure/retry.
  - [ ] Pass both fresh-volume and warm-cache runs on Apple Silicon.
  - [ ] Retain diagnostic failures but keep all generated/raw artifacts out of Git.
- [ ] **T25 Harden security/observability/operations** (depends: T24)
  - [ ] Cover input, model, path, cross-task, timeout, resource, and secret-redaction threats.
  - [ ] Trace one attempt across all containers and distinguish actionable health/error states.
  - [ ] Pass full regression suite plus manual secret/path/cross-task review.

### Checkpoint H

- [ ] T23-T25 pass from repository-root commands.
- [ ] All 12 specification success criteria have automated or named rehearsal evidence.
- [ ] Git contains no credential, raw imagery, generated result, report, log, or test artifact.

## Day 10 — Real rehearsal and handoff

- [ ] **T26 Rehearse and document delivery** (depends: T05, T14, T25; gates: G2-G4)
  - [ ] Record one real-LLM/real-raster run satisfying all success criteria with IDs/timings/checksums/evidence.
  - [ ] Prove refresh, forced failure/retry, warm cached/offline-data operation, and non-destructive stop.
  - [ ] Validate setup, preflight, demo, verification, troubleshooting, and teardown docs on target machine.

### Final checkpoint

- [ ] All task acceptance criteria and project Definition of Done are satisfied.
- [ ] `docker compose up --build` and all documented one-command checks pass on the target machine.
- [ ] Real rehearsal evidence is reviewed and no presentation-time imagery download is required.
- [ ] `git status --short` shows only intentional source/documentation changes.
