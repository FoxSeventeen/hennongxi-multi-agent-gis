# 运行与答辩排障

本手册遵循“先保留证据，再定位依赖，最后做最小恢复”。不要通过清卷、改数据库、改清单或创建新
任务来掩盖原任务失败。架构边界见 [`architecture.md`](architecture.md)，安装顺序见
[`setup.md`](setup.md)。

## 1. 安全诊断入口

先记录页面任务 ID、attempt、失败 Agent、错误码和时间，再执行：

```bash
docker compose ps
curl --silent --show-error http://127.0.0.1:8000/api/v1/health
curl --silent --show-error http://127.0.0.1:8000/api/v1/config/readiness
```

只看相关服务最近日志，不要一次导出全部历史：

```bash
docker compose logs --since=10m --tail=200 master-agent
docker compose logs --since=10m --tail=200 data-agent analysis-agent quality-agent publisher-agent
```

日志设计上已经脱敏，但在复制、截图或发给他人前仍要人工检查 Key、供应商 URL、个人信息、本地
路径和原始响应。优先用 `task_id`/`correlation_id` 搜索，不要用 Key 搜索后截屏。

## 2. 常见问题对照

### Docker 无法连接或权限被拒绝

**现象：**`Cannot connect to the Docker daemon`、`permission denied`，全部容器命令失败。

**处理：**确认 OrbStack/Docker Desktop 已启动且当前终端能执行 `docker version`。这是运行后端
问题，不是项目代码问题。不要删除 Docker socket、重装项目或清 volumes。

### `docker compose config --quiet` 失败

**现象：**变量、YAML 或插值错误。

**处理：**从 `.env.example` 重新核对变量名；真实值只留在 `.env`。不要修改 `docker-compose.yml`
来绕过错误。使用 `git diff -- docker-compose.yml .env.example` 确认源码是否被意外改动；不要执行
会输出完整展开环境的命令并把结果发给他人。

### 构建失败或架构不匹配

**现象：**镜像拉取失败、`exec format error`、目标平台不是 arm64。

**处理：**确认 `uname -m` 为 `arm64`，`.env` 中 `TARGET_PLATFORM=linux/arm64`，网络可访问镜像源，
磁盘空间充足。按 [`dependencies.md`](dependencies.md) 的固定版本构建，不临时升级基础镜像。

### PostGIS 启动但迁移失败

**现象：**`alembic upgrade head` 或 `current --check-heads` 非 0，Master 后续仓储报错。

**处理：**先确认 `postgis` 健康，再查看 Master 一次性迁移命令输出。迁移只向前；禁止运行
`alembic downgrade`、删除 `postgres-data`、手工改表或改写已有 revision。保留数据库卷并把提交
SHA、当前 revision、错误码交给开发者。

### 数据预检失败

**现象：**完整性、CRS、覆盖率、有效像元率或对齐出现 `[FAIL]`。

**处理：**根据逻辑 ID 向交付者重新取得批准文件，然后重跑预检。不要修改
`data/manifest.json`、重命名别的影像、关闭校验或在答辩当天运行下载脚本。四个文件全部通过后再
复制进 `data-cache` 卷。

### 就绪接口返回 blocker

| blocker | 含义 | 最小恢复 |
| --- | --- | --- |
| `LLM_NOT_CONFIGURED` | 三个 LLM 变量至少一个为空 | 在被忽略的 `.env` 修正后 `--force-recreate master-agent` |
| `DATA_NOT_CONFIGURED` | 镜像内清单不可读 | 确认当前提交/镜像，重新构建 Master；不要改清单路径 |
| `DEPENDENCY_UNAVAILABLE` | 至少一个 Agent/PostGIS/Redis 不健康 | 用聚合健康响应确定服务，再查看该服务最近日志 |

就绪只证明配置存在，不证明供应商鉴权有效，也不扫描大栅格；继续执行真实冒烟和数据预检。

### 大模型冒烟失败

| 退出码 | 处理 |
| ---: | --- |
| 1 | 读取脱敏 `error_code`/`retryable`；核对供应商状态、模型名、额度和计划格式 |
| 2 | 补全 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`，重建 Master |
| 3 | 记录提交 SHA 和最小输出，交给开发者；不要打开原始响应日志 |

401/403 通常是凭据或权限，429 通常是额度/限流，超时可能是网络或供应商状态。不要把真实 Key
粘贴到命令行验证，也不要无证据地把超时设置得很大。真实冒烟未通过时可以展示恢复规划能力，
但不能声称已满足“真实大模型验收”。

### 高德 Web 服务冒烟失败或页面显示“在线位置校验已降级”

高德是可选增强。检查 Key 是否配置、Web 服务类型/额度、供应商状态和网络；记录脱敏 code、
retryable、时间和耗时。不要把高德 Web端（JS API）Key 当作 Web 服务 Key，也不要把 Web 服务
Key 放入 `VITE_*` 前端变量。该能力只校验地名，不能用 POI/边界替换批准数据。

规范内神农溪任务应在降级后继续。如果因高德失败导致遥感链停止，保留任务证据并升级给开发者，
不要改研究区字符串绕过。

### 首页没有高德上下文地图或回退“地图已就位”

先区分正常降级和配置错误：`VITE_AMAP_JS_API_KEY` 或 `AMAP_JS_API_SECURITY_CODE` 任一为空时，
页面按设计不发出高德请求，直接显示离线占位图。任务按钮、时间线、重试和 MapLibre 成果不应受
影响；如果这些也不可用，应按其他服务故障排查，不能归因于高德底图。

如果两项都已配置仍回退，按顺序检查：

1. Key 类型必须是“Web端（JS API）”，安全密钥必须与它来自同一应用；Master 的
   `AMAP_WEB_SERVICE_KEY` 不能复用。
2. 高德控制台允许的域名/来源包含当前 `localhost`、`127.0.0.1` 或实际演示域名，额度和服务状态
   正常。
3. 修改 `.env` 后已执行 `docker compose up --detach --wait --force-recreate web`。
4. 浏览器开发者工具中只核对请求域名、状态码和耗时：Loader 应来自高德官方域名，坐标转换服务
   使用同源 `/_AMapService`。不要复制包含 Key 的完整 URL、查询串或响应。
5. 如果升级前的 Web 镜像在转换请求后固定等待 5 秒再回退，确认已经重建包含 JSONP MIME
   规范化修复的镜像；代理必须把合法回调作为 JavaScript 返回，同时继续保留 `nosniff`，不能
   通过关闭响应安全头来绕过。

`/_AMapService` 的 404/405 表示路径或方法不在白名单，403 表示来源检查失败，429 表示并发已满，
502/504 表示上游失败/超时，503 表示安全密钥缺失。不要把 `AMAP_JS_API_SECURITY_CODE` 改成
`VITE_*`、不要把安全密钥写入源码，也不要绕过同源代理直连转换接口。Loader、转换、鉴权或网络
失败应在 5 秒加载边界内保持离线占位；若出现白屏、无限重试或未处理 Promise，保留脱敏控制台
证据并升级给开发者。

### 页面显示系统不可用

先查看 `/api/v1/health`，找出具体服务。恢复命令使用服务名：

```bash
docker compose up --detach --wait <service-name>
```

不要直接重启全部服务作为第一反应；这样会丢失有用的现场时间关系。服务恢复后，同一任务若已经
失败，应从 UI 重试；活动任务会由 Master 的租约/启动恢复逻辑接管。

### 任务长时间停在某阶段

先确认页面仍在收到 SSE；刷新同一 URL，或直接查询：

```bash
curl --silent --show-error http://127.0.0.1:8000/api/v1/tasks/<task_id>
```

`<task_id>` 必须替换为页面 UUID。若查询在推进而页面不更新，属于 SSE/浏览器问题；页面会退避
轮询。若查询也不推进，按当前 Agent 检查健康与最近日志。不要重复点击创建任务。

### Data Agent 返回 `DATA_INVALID`

通常是 `data-cache` 卷缺文件或文件与清单不一致。先在宿主机重跑数据预检；通过后按
[`setup.md`](setup.md) 的固定四条 `docker compose cp` 重新复制批准文件，再重试原任务。不要把
宿主绝对路径传给 Agent。

### Analysis 或 Quality 失败

Analysis 失败时记录结构化错误码和栅格逻辑阶段，不手工编辑 `artifacts` 卷。Quality 失败说明
成果完整性、校验和、网格、值域、统计或阈值至少一项未通过；这是阻断发布的正确行为。不要跳过
Quality、修改 PASS 状态或直接调用 Publisher。修复输入/服务后使用 UI 安全重试。

### 地图空白或瓦片失败

先看任务是否已有合法成果。无任务、执行中或无成果失败时，应看到高德位置参考或离线占位；
不能把它们当作遥感成果。若整个地图区域空白，按上一节检查高德加载/回退和浏览器控制台。

任务为 `COMPLETED` 且 Quality 为 PASS 后，页面必须切换到 MapLibre，再检查 Publisher 健康。
浏览器开发者工具中只查看
失败请求的状态码，不复制带个人信息的完整会话。常见含义：

- 404：任务/成果身份不存在；确认 URL 中 task ID；
- 409/422：成果未通过、类型/坐标非法或契约不匹配；
- 5xx：Publisher 或成果卷异常，查看 Publisher 最近日志。

流域边界可见但栅格不可见时，分别切换三类图层并检查瓦片请求；不要用高德上下文地图掩盖非法
publication，也不要临时增加卫星、路线、天气、POI 或其他在线图层。

### PDF 无法下载或打开

确认任务完成、报告链接包含同一 task ID、响应为 200 和 `application/pdf`。失败时不要从别的任务
复制 PDF 冒充。恢复 Publisher 后重试原任务，重新生成的报告必须与当前 attempt 绑定。

### 刷新后任务消失

先确认 URL 仍含 `task_id`，再调用任务查询 API。若 API 404，记录任务 ID、提交 SHA、PostGIS
健康和 Master 日志，停止进一步写操作；不要清数据库或新建同名记录。若 API 200，则是前端恢复
问题，可保留 API 证据并切换已完成任务演示。

### E2E 失败

E2E 与日常项目使用不同 Compose project。查看：

```bash
docker compose -p hennongxi-e2e \
  -f docker-compose.yml -f tests/e2e/compose.yml ps
docker compose -p hennongxi-e2e \
  -f docker-compose.yml -f tests/e2e/compose.yml logs --tail=200
```

截图、Trace 和 HTML 位于 Git 忽略目录。E2E 使用假大模型/位置服务，并拦截高德 Loader；成功
和断网旅程都不访问真实高德，失败时不要怀疑或轮换真实 Key。只有明确需要冷启动时才清
`hennongxi-e2e` 的专用 volumes，绝不能省略 `-p` 后清卷。

### 内存、磁盘或容器被终止

检查 Docker/OrbStack 资源和宿主磁盘。Compose 已为容器设置硬上限；不要随意删除这些上限或让
浏览器测试与真实全栈同时抢占资源。先停止 E2E 项目和无关应用，再非破坏性重启失败服务。磁盘
不足时优先清理项目外可重建内容，禁止执行全局 `docker volume prune`。

## 3. 不可用的“快捷修复”

以下操作会破坏证据、安全边界或可恢复状态，排障时禁止：

- 删除/清空 PostGIS、Redis、数据缓存、成果或质量报告卷；
- 手工更新任务状态、attempt、事件、校验和或质量 PASS；
- 修改数据清单来匹配错误文件；
- 把真实 Key/安全密钥写进 Compose、源码、README 或终端命令；把 Web 服务 Key/安全密钥放进
  `VITE_*`，或把 Web端 Key 当作服务端 Key 使用；
- 暂时公开 Data/Analysis/Quality/PostGIS/Redis 端口；
- 跳过 Quality 直接发布；
- 用旧任务 PDF、截图或模拟数值冒充本次真实任务；
- 在答辩现场升级依赖、迁移版本或镜像标签。

## 4. 升级给开发者时提供什么

提供最小、脱敏的信息包：

- Git 完整提交 SHA、目标机架构和 Docker/Compose 版本；
- 发生时间、任务 ID、attempt、correlation ID；
- 当前状态、失败 Agent、结构化错误码、是否可重试；
- `docker compose ps` 的服务状态；
- 相关服务最近 10 分钟、最多 200 行且人工脱敏的日志；
- 能稳定复现的最短步骤，以及是否在在线/离线、高德启用/降级场景；
- 已尝试的非破坏性恢复和实际结果。

不要提供 `.env`、Key、供应商原始响应、完整数据库导出、原始影像或未脱敏截图。
