# 答辩演示操作手册

本文给实际答辩操作员使用。目标是让演示可重复、可解释、失败时不误操作，并留下 T28 所需的
真实证据。首次安装先完成 [`setup.md`](setup.md)，每次演练的记录填写到
[`verification.md`](verification.md) 的本地副本。

## 1. 演示原则

1. 正式演示使用真实缓存栅格和真实大模型；高德 Web 服务校验与浏览器上下文地图都是可选位置
   增强，不是主链依赖。
2. 不在答辩现场下载影像、升级依赖、构建新缓存或修改代码。
3. 不在投屏终端显示 `.env`、供应商控制台、完整日志或任何 Key。
4. 不删除失败任务；失败记录是系统诚实性的证据，修复依赖后从安全检查点重试。
5. 不执行 `docker compose down -v`，普通停止始终使用 `docker compose down`。

## 2. 人员分工

如果有两个人配合，建议：

- **讲解人**：按“问题—协作—结果—可信度”叙述，不操作终端；
- **操作员**：启动检查、浏览器输入、记录任务 ID、切换图层、下载报告和处理异常。

只有一人时，提前把健康检查终端、浏览器首页和本手册分别放在固定窗口，不要在投屏时搜索命令。

## 3. 答辩前一天：完整演练

### 3.1 确认版本与数据

```bash
git status --short --branch
git log -1 --oneline
uv run --frozen python scripts/data_preflight.py
docker compose config --quiet
```

记录提交 SHA。工作区应干净，数据预检 19 项全部 `[PASS]`。不要为追求“最新”临时拉取或升级。

### 3.2 初始化/迁移并启动

首次机器按 [`setup.md`](setup.md) 完整初始化；已初始化机器执行：

```bash
docker compose up --detach --wait postgis redis
docker compose run --rm --no-deps master-agent alembic upgrade head
docker compose run --rm --no-deps master-agent alembic current --check-heads
docker compose up --detach --wait
```

检查：

```bash
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/config/readiness
```

必须满足八个常驻服务健康、聚合状态 `HEALTHY`、配置 `ready: true` 且无 blocker。

### 3.3 真实上游冒烟

```bash
docker compose run --rm --no-deps master-agent \
  python -m hennongxi_master.llm_smoke
docker compose run --rm --no-deps master-agent \
  python -m hennongxi_master.amap_smoke
```

第一条必须退出 0。第二条只验证 Master 的高德 Web 服务地名校验，并且只在答辩计划展示该能力
时要求退出 0；否则记录未配置/降级状态，不要因此改变遥感数据。浏览器的高德上下文地图使用另一
个 Web端（JS API）Key 与安全密钥：如计划展示，打开首页确认普通道路图和官方 Logo/版权可见，
但不要保存地图截图。把允许的脱敏字段抄到本地验收记录，不保存终端中其他输出。

### 3.4 自动化基线

E2E 使用独立的 `hennongxi-e2e` Compose 项目、确定性小栅格、假大模型、假位置服务和被路由
拦截的假高德 Loader，不消耗真实额度，也不会覆盖日常项目的命名卷。

冷启动前只删除 **E2E 专用项目**：

```bash
docker compose -p hennongxi-e2e \
  -f docker-compose.yml -f tests/e2e/compose.yml \
  down --volumes --remove-orphans
./tests/e2e/run.sh
```

预期 Playwright `5 passed`。保留 E2E volumes，再执行一次温缓存复跑：

```bash
./tests/e2e/run.sh
```

仍应为 `5 passed`。失败诊断只保存在 Git 忽略的 `tests/e2e/test-results/` 和
`tests/e2e/playwright-report/`；不要把这些目录提交。

### 3.5 完成一次真实 UI 主链

打开 [http://localhost:3000](http://localhost:3000)，确认标题为“神农溪生态监测指挥台”、状态为
“系统已就绪”。已配置独立 Web端凭据时，此刻还应显示高德普通道路图和“高德位置参考”；没有
配置或加载失败时应显示“地图已就位”的离线占位图，两种状态都不影响创建任务。输入：

> 分析巴东县神农溪 2019 至 2024 年植被变化

点击“创建监测任务”后立刻记录：

- 浏览器 URL 中的 `task_id`；
- 开始时间；
- 页面显示的在线位置校验状态；
- 任务执行期间高德位置参考是否保持同一地图且文案显示任务短 ID；
- 规划来源是“真实大模型规划”还是“恢复规划”。

时间线应按顺序显示五个 Agent 阶段：规划、数据准备、分析、质量检查、发布。所有阶段使用同一
任务 ID；状态只能向前推进或诚实失败，不能跳到虚假完成。

任务完成后逐项检查：

- 页面显示“任务已完成”和“质量结论：通过”；
- 高德实例/位置参考已经消失，页面切换为 MapLibre 成果地图；
- 前期 NDVI、后期 NDVI、NDVI 差值按钮均可切换，流域边界始终可见；
- 日期为 2019-08-19 与 2024-08-12，面积单位为公顷；
- 四项质量指标同时出现：覆盖率、有效像元率、成果完整性、分析耗时；
- 页面明确显示真实大模型证据；
- 中文 PDF 链接可下载，文件可打开，任务 ID、日期、统计、质量与页面一致。

完成后刷新浏览器。URL 中同一个 `task_id` 应保留，任务、时间线、地图、指标和报告链接应从
持久化状态重建。不要用新任务替代刷新验证。

### 3.6 温缓存真实复跑

保持 volumes 不变，再创建一个相同查询。记录第二个任务 ID 和耗时。两次任务身份不同，但批准的
日期、四个分析栅格的内容校验和、面积统计和质量门禁应一致；PDF 包含任务身份，PDF 校验和可以
不同。温缓存复跑期间不应下载影像。

## 4. 答辩前 30 分钟：短检查

```bash
git status --short --branch
docker compose up --detach --wait
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/config/readiness
```

然后：

1. 打开首页，确认系统已就绪；
2. 打开前一天已完成任务的 URL，确认地图和报告仍可访问；
3. 检查磁盘、电源和网络，关闭系统更新、休眠和无关下载；
4. 保留已完成任务作为网络或供应商突发故障时的可验证备用；
5. 不再运行全量构建、依赖升级、冷卷 E2E 或真实影像缓存脚本。

## 5. 建议的 8 分钟讲解顺序

| 时间 | 页面动作 | 讲解重点 |
| --- | --- | --- |
| 0:00–0:40 | 首页、上下文地图与就绪卡片 | 高德只提供位置参考；五 Agent 内部服务不公开端口 |
| 0:40–1:20 | 输入中文任务 | 大模型只生成受约束计划，不能执行任意代码/路径 |
| 1:20–2:40 | 观察时间线 | 同一任务 ID 贯穿规划、数据、分析、质量和发布 |
| 2:40–4:20 | 切换三种地图图层 | 展示完整流域、两期真实 NDVI 与差值，不是模拟数值 |
| 4:20–5:30 | 面积与质量面板 | Quality 独立复核覆盖、有效像元、完整性和耗时 |
| 5:30–6:20 | 下载 PDF | 报告与任务、日期、统计、质量及校验和绑定 |
| 6:20–7:10 | 刷新页面 | PostGIS 保存事实，Redis 丢失也能降级重建 |
| 7:10–8:00 | 展示已演练失败/重试证据 | 失败不伪装成功，重试保留旧 attempt 并安全复用检查点 |

不要把时间花在逐行讲代码。评委需要看到“为什么结果可信、为什么失败可解释、为什么外部服务
断开仍不会改变批准的数据边界”。

## 6. 强制失败与 UI 重试演练

这一节应在正式答辩前演练并记录，正式现场优先展示已有证据，不建议为了效果临时停服务。

1. 保持全部服务健康，在 Web 创建一个新的真实任务；
2. 任务进入规划或数据准备后，在终端执行：

```bash
docker compose stop publisher-agent
```

3. 任务应最终在发布阶段显示 `FAILED`，页面必须没有质量“通过”冒充的完整结果，也不能提供新
   PDF；高德位置参考可以继续显示，但必须明确不是遥感成果。记录任务 ID、attempt 1、失败
   Agent、错误码和页面截图；如果在线高德图可见，不保存该地图截图；
4. 恢复 Publisher：

```bash
docker compose up --detach --wait publisher-agent
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
```

5. 健康恢复后，在同一任务页面点击“重试失败任务”；
6. 页面应显示“第 2 次执行”，保留第一次失败证据，重新验证并复用安全检查点，最终完成。

如果第一次任务在停服务前已经完成，它不能作为失败证据；创建新任务重做，不要修改数据库状态。
不要通过删除成果、篡改校验和或终止数据库来制造失败。

## 7. 高德降级与离线主链演练

高德有“Master Web 服务地名校验”和“浏览器 JS API 上下文地图”两条独立边界，记录时不能把
任一 Key 未配置、供应商断网和另一条能力混为一谈。

### 7.1 Master Web 服务 Key 未配置

暂时把本机 `.env` 中 `AMAP_WEB_SERVICE_KEY` 置空，重建 Master：

```bash
docker compose up --detach --wait --force-recreate master-agent
```

创建规范内任务。页面应显示在线位置校验已降级，但真实大模型与缓存遥感链仍能完成。该步骤证明
“没有 Key 也能运行”，不等价于供应商网络故障。演练后恢复 Key 并再次重建 Master。

### 7.2 浏览器 Web端凭据未配置

在不投屏时暂时把 `.env` 中 `VITE_AMAP_JS_API_KEY` 与 `AMAP_JS_API_SECURITY_CODE` 置空，重建
Web：

```bash
docker compose up --detach --wait --force-recreate web
```

刷新首页。页面应直接显示“地图已就位”的离线占位图，浏览器不应请求高德域名或
`/_AMapService`，创建任务按钮仍可用；创建一个规范内任务并确认它完成、MapLibre 成果和报告
正常。该步骤只证明未配置回退，不等价于真实网络故障。演练后恢复两个值并再次重建 Web。

### 7.3 真实外网断开

在全部容器健康、真实在线主链已经成功且批准数据已缓存的前提下，由操作员在系统设置中临时断开
目标机外网，但保持 OrbStack/Docker 和本地 Compose 网络运行。不要用修改 `/etc/hosts`、防火墙
或容器网络的临时命令代替。

此时创建同一规范内任务：

- Master 高德 Web 服务应记录可观测降级；
- 浏览器高德上下文地图应在 5 秒加载边界内回退“地图已就位”，页面没有白屏或未处理异常；
- 大模型也可能因外网不可达而使用明确标记的恢复计划；等待已配置超时安全结束；
- Data→Analysis→Quality→Publisher 应使用缓存数据继续；
- 日期、分析栅格内容、统计和质量门禁应与在线运行一致；
- 页面不得把恢复计划标成真实大模型调用。

任务进入终态后恢复外网，执行健康检查、两个真实上游冒烟，并刷新首页确认高德上下文地图恢复。
该步骤证明离线主链和两条高德边界的真实降级，但不替代至少一次真实大模型成功证据；如果断网前
没有使用有效的 Web 服务 Key、Web端 Key 和安全密钥，也不能把结果记为对应高德真实断网证据。

## 8. 现场异常处理顺序

1. 保持当前页面和任务 ID，不刷新掉错误信息；
2. 看页面错误码和失败 Agent，再执行 `docker compose ps`；
3. 只查看相关服务最近 10 分钟、最多 200 行日志；
4. 修复明确依赖后优先点击同一任务的重试，不新建任务掩盖失败；
5. 两分钟内无法恢复时，切换到前一天已完成任务，说明现场外部依赖状态并展示持久化成果；
6. 答辩后把脱敏证据补充到本地验收记录。

具体命令与故障对照见 [`troubleshooting.md`](troubleshooting.md)。

## 9. 演示结束

记录结束时间、最终任务 ID 和结论，然后非破坏性停止：

```bash
docker compose down
git status --short
```

预期 Git 只显示演示前已有的预期源码/文档变更；`.env`、栅格、报告、日志和截图不应出现。保留
命名卷以便复核。验收证据放在 Git 忽略的 `tmp/t28/` 或项目外的交付目录，经脱敏后再决定是否
形成正式记录。
