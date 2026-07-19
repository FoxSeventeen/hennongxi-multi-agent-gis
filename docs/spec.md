# Spec: 神农溪分布式多 Agent GIS 演示系统

## Objective

在 10 天内从零构建一套可在答辩现场稳定运行的中文 Web 系统。用户用自然语言发起“神农溪生态变化监测”任务，Master Agent 调用真实大模型生成受约束的执行计划，并协调多个独立 Linux 容器中的 Agent，围绕同一个 `task_id` 完成数据准备、双时相 NDVI 计算、质量评价、地图发布与报告生成。

系统面向实习答辩教师。验收重点是功能真实、过程可视、结果可信：教师应能看到任务如何被规划、在不同 Agent 之间传递、异步执行并产出可交互地图和可下载报告，而不需要阅读源代码。

### Primary user journey

1. 用户在中文界面输入生态变化监测需求。
2. Master Agent 调用配置好的真实大模型，将需求转换为结构化执行计划。
3. 系统创建唯一 `task_id`，界面开始显示 Agent 时间线与实时进度。
4. Data Agent 校验完整神农溪流域边界和本地双时相遥感数据。
5. Analysis Agent 异步计算两期 NDVI 及 NDVI 变化栅格。
6. Quality Agent 检查空间范围、有效像元、结果完整性和运行耗时。
7. Publisher Agent 发布地图图层并生成中文监测摘要报告。
8. Master Agent 汇总结果；前端显示地图、指标、质量结论和报告下载入口。

## Functional requirements

### Required for the MVP

- 中文、地图优先的单页 Web 界面。
- 自然语言任务输入，以及真实大模型调用记录。
- 大模型输出结构化计划；计划必须经过 Pydantic Schema 和允许步骤白名单校验。
- 独立运行的 Master、Data、Analysis、Quality、Publisher Agent 服务。
- 所有跨 Agent 调用均通过网络接口完成，不得以同一进程内函数调用冒充节点协作。
- 全链路携带 `task_id`，并记录 Agent、步骤、状态、进度、耗时和错误。
- 耗时分析异步执行；前端通过 Server-Sent Events 接收进度，并提供 HTTP 查询兜底。
- 使用真实双时相红光/近红外数据计算 NDVI，不使用随机数或预制结果冒充计算。
- 地图显示完整神农溪流域边界，以及裁剪、降采样后的 NDVI 和变化结果。
- 质量评价至少包含：范围覆盖率、有效像元比例、输出完整性和运行耗时。
- 输出至少包含：两期 NDVI、NDVI 差值、面积统计、质量结论和中文 PDF 报告。
- `docker compose up --build` 可启动完整系统。

### Explicitly out of scope for the 10-day MVP

- 服务招标、竞价和协商。
- 灾害预警、水文分析、土地利用等其他 GIS 场景。
- 用户注册、登录、角色和权限管理。
- 多租户、生产级高可用和自动扩缩容。
- 答辩现场下载原始遥感数据。
- 原生 macOS 进程部署；所有服务统一运行在 Linux 容器中。
- 为无编程基础人员优化底层代码讲解。

## Architecture and Tech Stack

### Runtime topology

| Component | Responsibility | Internal port |
| --- | --- | ---: |
| Web | 中文任务面板、Agent 时间线、地图与报告入口 | 3000 |
| Master Agent | LLM 规划、任务编排、状态聚合、公开 API | 8000 |
| Data Agent | 研究区与双时相影像校验、数据清单 | 8001 |
| Analysis Agent | NDVI、差值、分级与统计计算 | 8002 |
| Quality Agent | 结果范围、完整性、有效像元和耗时评价 | 8003 |
| Publisher Agent | 栅格瓦片、成果元数据和 PDF 报告 | 8004 |
| PostgreSQL/PostGIS | 研究区、任务、步骤、成果元数据 | 5432 |
| Redis | 任务状态、事件流和短期缓存 | 6379 |

前端仅访问 Master Agent 和 Publisher 的地图/文件资源。其余 Agent 不直接暴露给宿主机，容器之间通过 Compose 网络按服务名通信。

### Proposed stack

- Frontend: React, TypeScript, Vite, MapLibre GL JS.
- Backend services: Python 3.12, FastAPI, Pydantic, HTTPX.
- GIS: Rasterio, NumPy, GeoPandas/Shapely, Rio-Tiler.
- Storage: PostgreSQL with PostGIS, Redis, Docker named volumes.
- Report: ReportLab with bundled Chinese font.
- Tests: Pytest for Python services, Vitest and Testing Library for Web, Playwright for the critical demonstration journey.
- Packaging: Docker Compose with `linux/arm64` compatible images on the current Apple Silicon Mac.

Dependency versions will be pinned only after checking their current official documentation and container availability.

### Task state model

`PENDING -> PLANNING -> DATA_PREPARING -> ANALYZING -> QUALITY_CHECKING -> PUBLISHING -> COMPLETED`

Any active state may transition to `FAILED`. Every transition records timestamp, Agent name, progress, message and optional artifact metadata.

### LLM boundary

- Configuration comes only from environment variables: `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`.
- The API Key is never sent to the browser, stored in PostGIS, logged or committed.
- The model produces intent and a JSON plan; it cannot generate arbitrary shell commands, file paths or SQL.
- Only the fixed ecological-monitoring steps are executable in the MVP.
- Invalid or unavailable model responses produce a visible error; a deterministic built-in plan may be used for system recovery, but a successful acceptance demonstration must include at least one real model call.

## API and Event Contracts

The detailed OpenAPI contract will be written before endpoint implementation. The stable public surface is:

- `POST /api/v1/tasks` - create a natural-language monitoring task; returns `202` and `task_id`.
- `GET /api/v1/tasks/{task_id}` - return current state, progress, plan, steps and artifacts.
- `GET /api/v1/tasks/{task_id}/events` - Server-Sent Events stream for state changes.
- `POST /api/v1/tasks/{task_id}/retry` - retry a failed task from a safe checkpoint.
- `GET /api/v1/health` - aggregate health of required Agent services.
- `GET /api/v1/config/readiness` - indicate whether data and LLM configuration are ready without exposing secrets.

Internal Agent endpoints use versioned request/response schemas and always include `task_id`, `step_id` and correlation metadata.

## Commands

Commands are executed from the repository root.

```bash
# First-time configuration
cp .env.example .env

# Start the full system
docker compose up --build

# Stop without deleting cached data
docker compose down

# Run all backend tests
docker compose run --rm master pytest

# Run frontend unit tests
docker compose run --rm web npm test -- --run

# Run lint and type checks
docker compose run --rm master ruff check .
docker compose run --rm web npm run lint
docker compose run --rm web npm run typecheck

# Run the critical browser journey after the stack is healthy
docker compose run --rm e2e npm test
```

Exact test container names may be refined in the approved implementation plan, but the final repository must expose equivalent one-command checks.

## Project Structure

```text
apps/
  web/                    React task dashboard and map UI
services/
  master/                 Public API, LLM adapter and orchestration
  data_agent/             Dataset and study-area preparation
  analysis_agent/         NDVI and change computation
  quality_agent/          Result validation and scoring
  publisher_agent/        Tiles, artifacts and PDF report
packages/
  contracts/              Shared versioned Pydantic schemas
  observability/          Correlation-aware structured logging
infra/
  db/                     PostGIS initialization and migrations
data/
  boundaries/             Versioned lightweight study-area boundary
  samples/                Manifest for local demo rasters
  outputs/                Generated artifacts; ignored by Git
tests/
  integration/            Cross-service contract and workflow tests
  e2e/                    Browser demonstration journey
docs/
  spec.md                 Approved product and engineering contract
tasks/
  plan.md                 Dependency-ordered implementation plan
  todo.md                 Small verifiable work items
docker-compose.yml
.env.example
```

## Code Style

Python uses typed async service boundaries and explicit schemas:

```python
class TaskEvent(BaseModel):
    task_id: UUID
    step_id: str
    agent: AgentName
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    message: str


async def publish_event(event: TaskEvent) -> None:
    await event_store.append(event)
```

- Python: Ruff formatting/linting, type hints on public functions, snake_case modules and functions.
- TypeScript: strict mode, ESLint, functional React components, PascalCase components and camelCase values.
- API payloads: snake_case JSON, UTC ISO-8601 timestamps, stable enums, structured error objects.
- User-visible text: Chinese; identifiers, logs and API fields: English.

## Testing Strategy

- Unit tests cover NDVI math, plan validation, task transitions, quality metrics and error mapping.
- Contract tests verify every Agent request and response against shared schemas.
- Integration tests run a small deterministic raster fixture through the full Agent chain without a real LLM.
- LLM adapter tests use a local fake HTTP server; a separate opt-in smoke test verifies a real configured API.
- Browser tests verify task creation, progress rendering, map layer activation, completion metrics and report download.
- The final demonstration dataset receives one recorded full-stack rehearsal before delivery.
- Critical pure logic targets at least 90% branch coverage; generated UI and container glue are judged by integration and browser tests rather than a global coverage percentage.

## Boundaries

### Always do

- Validate all external inputs and LLM outputs.
- Propagate `task_id` and correlation metadata through every service call and log.
- Keep sample-data provenance and acquisition dates in a manifest.
- Run the relevant unit, contract and integration tests before each implementation commit.
- Keep `.env`, API keys, downloaded raw imagery and generated outputs out of Git.

### Ask first

- Add a second analysis scenario or user-facing workflow.
- Replace the selected LLM API contract with a provider-specific SDK.
- Change database schema after the first migration is approved.
- Add a new infrastructure dependency beyond PostGIS and Redis.
- Expand the public API beyond the versioned routes above.

### Never do

- Commit credentials, access tokens or private download URLs.
- Execute model-generated shell, SQL or arbitrary Python code.
- Present mocked NDVI values as real analysis results.
- Make the live demonstration depend on downloading large GIS datasets.
- Hide Agent failures by marking incomplete tasks as successful.

## Success Criteria

The MVP is accepted only when all of the following are demonstrated:

1. `docker compose up --build` starts the required services on the current machine.
2. Health/readiness UI identifies each independent Agent and its network status.
3. A Chinese natural-language request produces a valid plan through a real configured LLM API.
4. One `task_id` is visible across the complete Agent timeline and persisted task record.
5. The Analysis Agent calculates two NDVI rasters and one difference raster from real red/NIR input bands.
6. The map displays the complete watershed boundary and the computed raster results.
7. The Quality Agent reports coverage, valid pixels, completeness and elapsed time.
8. The Publisher Agent produces a downloadable Chinese PDF report tied to the same task.
9. The workflow completes from the UI without manual backend intervention and survives a page refresh.
10. A forced Agent failure is shown honestly and can be retried from the UI.
11. Unit, contract, integration and critical browser tests pass.
12. The final workflow is rehearsed with cached data and does not need to download imagery during the demonstration.

## Open Questions

- The exact LLM provider, Base URL and model name will be supplied later through environment configuration.
- The authoritative watershed-boundary and imagery sources will be selected after official-source verification; their provenance will be recorded in `data/manifest.json`.
- The current Docker-compatible OrbStack backend is installed but not running. It must be started before container verification.
