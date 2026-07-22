# TODO: 神农溪分布式多 Agent GIS 演示系统

Source of truth: `docs/spec.md` at `92e50a6`. Detailed rationale, file scope, and commands: `tasks/plan.md`.

## How to use this checklist

- Work in task-number order unless `tasks/plan.md` explicitly marks work as parallel-safe.
- Mark a task complete only when its acceptance and verification sub-items pass.
- Stop at every checkpoint for a green repository-wide check and review.
- Keep raw imagery, `.env*`, credentials, generated rasters/reports, logs, and test artifacts out of Git.
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
- [x] G5：已批准后端专用、可降级的高德研究区校验边界；详见
  `tasks/approvals/G5-amap-web-service-integration.md`。
- [x] G6：已批准高德普通道路上下文地图、任务期间持续显示、成果态切回 MapLibre，以及
  高德不可用时回退现有离线占位图；详见 `tasks/approvals/G6-amap-context-map.md`。

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

## 第 7 天——任务与时间线 Web 界面

- [x] **T19 构建地图优先的外壳、任务与就绪界面**（依赖：T01-T02、T15）
  - [x] 提供可访问、响应式的中文就绪状态、任务输入、加载和错误界面。
  - [x] 使用经契约校验的 API 类型并阻止重复提交。
  - [x] 通过组件测试、lint、类型检查以及键盘、焦点和对比度检查。
- [x] **T20 渲染实时 Agent 时间线**（依赖：T17、T19）
  - [x] 以中文显示任务编号、有序 Agent、步骤、状态、进度、耗时和错误。
  - [x] SSE 断开后通过轮询与退避策略恢复，并在页面刷新后重建任务。
  - [x] 通过时间线、SSE、轮询、刷新测试及干净浏览器运行检查。

### 检查点 G1

- [x] T19-T20 在笔记本与窄视口尺寸下通过验证。
- [x] 从任务创建到实时进度的流程可以承受断线和刷新。

## 第 8 天——地图与结果界面

- [x] **T21 显示流域与计算图层**（依赖：T12、T19-T20）
  - [x] 适配完整流域，并在边界始终可见的情况下切换前后期 NDVI 与差值图层。
  - [x] 匹配 Publisher 的日期、边界、图例、颜色、单位和数据归属，并处理瓦片错误。
  - [x] 通过组件测试以及真实浏览器的瓦片网络、控制台和视觉检查。
- [x] **T22 展示成果、报告与重试**（依赖：T13-T14、T18、T20）
  - [x] 展示双时相日期、NDVI 变化面积、四项质量指标与结论、真实大模型证据和中文 PDF。
  - [x] 明确区分真实/恢复规划以及失败、未完成、完成状态，不把部分结果伪装成成功。
  - [x] 通过报告下载、桌面/窄视口和 Publisher 强制失败后界面安全重试的真实浏览器检查。

### 检查点 G2（第 8 天界面验收）

- [x] T21-T22 的测试、Lint、类型、构建和契约检查通过，浏览器控制台与必需网络请求干净。
- [x] 答辩教师无需阅读代码即可理解任务、Agent 链、地图、质量、失败、重试和报告。

## 第 9 天——高德位置校验与确定性全链证明

- [x] **T23 建立安全、可降级的高德 Web 服务适配器**（依赖：T01、T03、T14；门禁：G5）
  - [x] Key 仅注入 Master；固定 HTTPS 域名、严格超时/响应上限/JSON 校验且不记录原始响应。
  - [x] 假服务覆盖成功、零/多结果、认证、限流、超时、重定向、压缩/超大响应和密钥脱敏。
  - [x] 显式真实冒烟只输出脱敏状态并返回 `infocode=10000`。
- [x] **T24 把研究区地名校验接入 Master 规划流程**（依赖：T05、T15-T18、T23）
  - [x] 神农溪/巴东县查询产生验证通过证据，但仍只使用 G2 流域、影像与计算参数。
  - [x] 明确指向其他地区的请求不再静默绑定神农溪；模糊结果不伪装成已验证。
  - [x] 高德未配置、超时或限流时，规范内任务可观测降级并继续离线主链。

### 检查点 H1（高德接入边界）

- [x] T23-T24 的单元、契约、类型和 Compose 定向验证通过。
- [x] 浏览器、日志、PostGIS、Redis、报告与 Git 不含高德 Key 或原始响应。
- [x] 高德可用/降级两次运行的 G2 确定性分析与质量门禁一致，位置证据如实不同。

- [x] **T25 证明契约和确定性完整 Agent 链**（依赖：T08、T10-T18、T24）
  - [x] 全部公开/内部载荷通过同一共享契约与 OpenAPI。
  - [x] 假 LLM/高德服务下的验证与降级链产生数学可复现的栅格、统计、质量和报告。
  - [x] 非目标地区、非法计划、损坏数据、不可达 Agent 和部分成果均不产生虚假成功。

## 第 10 天——浏览器验收、加固与交付

- [x] **T26 自动化关键 Compose 浏览器旅程**（依赖：T04-T05、T19-T22、T25；门禁：G4）
  - [x] 一键验证就绪、位置证据、完成、地图、指标、报告、刷新、失败与重试。
  - [x] Apple Silicon 上全新 volume 和温缓存复跑均通过，E2E 不调用真实高德。
  - [x] 保留失败诊断，但密钥、原始数据、高德响应与生成成果不进入 Git。
- [x] **T27 执行安全、可观测性和运行加固**（依赖：T26）
  - [x] 覆盖输入、模型/高德响应、SSRF、路径、跨任务、超时、资源和密钥脱敏威胁。
  - [x] 跨容器追踪同一尝试，并区分必需依赖故障与高德可选增强降级。
  - [x] 全量回归及人工密钥、路径、跨任务和高德数据留存检查通过。
- [ ] **T29 接入高德上下文地图并保留 MapLibre 成果地图**（依赖：T21-T22、T27；门禁：G6）
  - [x] 分离 Web JS API Key、服务端安全密钥和既有 Web 服务 Key；实现固定同源、只读、
    有路径/超时/响应/并发上限且不泄密的安全代理。
  - [x] 无任务、任务执行中和无成果失败态显示高德普通道路地图；未配置、超时、鉴权或网络
    失败时在 5 秒内稳定回退现有离线占位图，且不阻断任务。
  - [x] 合法成果到达后销毁高德实例并复用既有 MapLibre 三类成果图层；非法成果继续显示
    “地图图层暂不可用”，不让高德背景掩盖成果错误。
  - [x] 组件和默认 E2E 使用假 Loader/网络拦截；凭据扫描、无留存检查和中文交付文档已完成。
  - [x] 使用独立可轮换 Web端（JS API）Key/安全密钥完成真实在线浏览器冒烟；道路图、官方
    标识、任务期间复用、成果切换、网络边界和干净控制台均已验证，未保存高德截图或密钥。
  - [ ] 断开真实高德网络验证 5 秒回退和离线任务主链，再恢复网络并刷新确认高德恢复；完成前
    不得勾选本任务。
- [ ] **T28 演练真实答辩并完成交付文档**（依赖：T05、T14、T27、T29；门禁：G2-G6）
  - [ ] 记录真实 LLM/栅格/高德冒烟运行的 ID、耗时、校验和与证据。
  - [ ] 证明刷新、强制失败/重试、温缓存及关闭高德网络后的离线主链。
  - [ ] 在目标机器验证配置、预检、演示、状态码/额度、Key 轮换、排障与非破坏性停止文档。
    - [x] 中文 README、架构、安装、答辩、验收模板和排障文档已完成静态核对。
    - [ ] 接手操作员在目标机器逐项执行并由第二人复核；未验证前父项保持未完成。

### 检查点 H2 与最终检查点

- [ ] T25-T29 可从仓库根目录通过文档化命令执行。
- [ ] 规格 12 项成功标准均有自动化或具名演练证据，Definition of Done 全部满足。
- [ ] `docker compose up --build` 与所有一键检查在目标机器通过。
- [ ] 演示不依赖下载影像或高德在线可用；`git status --short` 只含预期源码/文档。
