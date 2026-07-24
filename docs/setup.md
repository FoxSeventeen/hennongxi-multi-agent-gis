# 目标机器安装与配置

本文档适用于第一次接手项目的人，在目标 Apple Silicon 机器上准备真实演示环境。

- 日常快速启动指南见 [`README.md`](../README.md)
- 具体答辩演示步骤见 [`demo-runbook.md`](demo-runbook.md)

所有命令均从仓库根目录执行。

## 1. 交付物清单

接手人应通过不同渠道拿到三类内容：

1. **Git 仓库源码**
2. **不进入 Git 的四个已批准 GeoTIFF 文件**
3. **不进入 Git 的配置信息**：
   - 大模型配置（必需）
   - 高德配置（可选）：如启用高德，需要可轮换的 Web 服务 Key、独立的 Web 端（JS API）Key 和与后者配套的安全密钥

**重要检查**：
源码仓库不应包含 `.env`、真实 Key、原始/缓存影像、生成栅格、PDF、日志、Playwright 截图或 Trace。

开始前执行检查：

```bash
git status --short --branch
git log -1 --pretty=fuller
git config user.email
```

**预期结果**：
- 工作区干净
- 提交者邮箱为项目所有者指定的 `1661863496@qq.com`
- 如果只负责演示而不修改代码，不需要创建新提交

## 2. 环境要求

目标组合与具体版本记录在 [`dependencies.md`](dependencies.md)：

- **硬件**：Apple Silicon / `linux/arm64` 架构
- **容器环境**：已启动的 OrbStack 或 Docker Desktop
- **Docker Compose**：支持 `--wait` 参数
- **Git**：用于克隆代码
- **Python 3.12、uv 0.11 系列**：运行数据预检和本地检查
- **Node.js 24 与 pnpm 11**：只在脱离容器开发 Web 时需要（演示不需要）

检查基础工具：

```bash
uname -m
docker version
docker compose version
python3 --version
uv --version
```

**预期输出**：
- `uname -m` 应输出 `arm64`
- 其他命令应正常显示版本号

**常见问题**：
- 如果 Docker 命令无法连接守护进程，先启动 OrbStack/Docker Desktop
- 不要反复重建或删除 volumes

## 3. 本地配置

创建被 Git 忽略的配置文件：

```bash
cp .env.example .env
git check-ignore -v .env
```

第二条命令必须显示 `.gitignore` 规则。

编辑 `.env` 时保留其余本地默认值，只填写真实配置：

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | **是** | 大模型接口的访问令牌，只注入 Master |
| `LLM_BASE_URL` | **是** | 大模型接口的基础 URL，必须使用可信 HTTPS 地址 |
| `LLM_MODEL` | **是** | 供应商实际支持的模型名 |
| `LLM_TIMEOUT_SECONDS` | 否 | 默认 30 秒，答辩前不要无证据地放大 |
| `AMAP_WEB_SERVICE_KEY` | 否 | 高德 Web 服务 Key，只用于研究区地名校验 |
| `AMAP_TIMEOUT_SECONDS` | 否 | 默认 3 秒，保证可选增强不会长时间阻塞 |
| `VITE_AMAP_JS_API_KEY` | 否 | 独立的高德 Web 端（JS API）Key；按官方机制会进入浏览器 |
| `AMAP_JS_API_SECURITY_CODE` | 否 | 与 JS API Key 配套的安全密钥，只注入 Web 服务端同源代理 |
| `VITE_AMAP_LOAD_TIMEOUT_MS` | 否 | 默认 5000 毫秒；有效范围 1000–15000，异常值回到默认值 |

**安全提醒**：
- 只有 `VITE_AMAP_JS_API_KEY` 允许作为浏览器可见变量
- 不要把 Web 服务 Key 或安全密钥放入任何 `VITE_*` 变量
- 不要在终端执行带真实值的 `export ...` 命令
- 不要把配置粘贴到 issue、截图、演示文稿或验收文档
- Key/安全密钥曾经出现在聊天、屏幕共享或其他公开位置时，交付前必须在供应商控制台轮换
- 轮换后只更新本机 `.env` 并重建对应容器

### 申请并配置高德两类能力

如果要展示高德地图功能，按以下步骤申请：

1. 登录 [高德开放平台控制台](https://console.amap.com/dev/index)，按平台要求完成开发者认证，创建或选择本项目专用应用

2. **为浏览器地图新增 Web 端（JS API）Key**：
   - 新增一个”Web 端（JS API）”Key
   - 记录控制台签发的 `securityJsCode`（安全密钥）
   - 将 Key 写入 `VITE_AMAP_JS_API_KEY`
   - 将安全密钥写入 `AMAP_JS_API_SECURITY_CODE`
   - **注意**：二者必须来自同一应用/配置

3. **如需 Master 的研究区地名校验，再单独新增 Web 服务 Key**：
   - 单独新增一个”Web 服务”Key
   - 写入 `AMAP_WEB_SERVICE_KEY`
   - **重要**：即使控制台允许，也不要把这个 Key 复用于浏览器地图

4. **配置域名白名单**：
   - 本机开发使用 `localhost` 和 `127.0.0.1`
   - 正式部署只填真实域名，不能使用任意域名通配
   - 检查 JS API 和 Web 服务各自的配额、权限和到期/轮换计划

**官方文档**：
- [Web 端（JS API）准备工作](https://lbs.amap.com/api/javascript-api-v2/prerequisites)
- [安全密钥使用说明](https://lbs.amap.com/api/javascript-api-v2/guide/abc/jscode)

**重要提醒**：
本项目不会采用把 `securityJsCode` 明文写入前端的方案。浏览器只设置同源 `/_AMapService`，代理在服务端追加安全密钥。Web 端 Key 本身不是服务端秘密，但仍必须专用、受域名限制并在曝光后轮换。

检查配置文件仍未被 Git 跟踪：

```bash
git status --short
git ls-files .env
```

第二条命令应无输出。

## 4. 准备并预检真实数据

把交付者提供的四个文件放到正确位置：

```text
data/cache/demo/before_red.tif
data/cache/demo/before_nir.tif
data/cache/demo/after_red.tif
data/cache/demo/after_nir.tif
```

**重要**：不要从不明网盘、搜索结果或临时 URL 替换这些文件。

执行完全离线的预检：

```bash
uv run --frozen python scripts/data_preflight.py
```

**预期结果**：19 行检查全部为 `[PASS]`，包括：
- 五项完整性检查
- 流域 GIS 检查
- 四栅格元数据检查
- 覆盖率检查
- 有效像元率检查
- 统一网格检查

**数据来源**：事实来源是 [`../data/manifest.json`](../data/manifest.json)，不要手工修改清单来适配错误文件。

**如预检失败**：停止安装并查看 [`troubleshooting.md`](troubleshooting.md)。**答辩当天禁止运行 `scripts/cache_demo_data.py`**，该脚本会访问外部数据源，且重新生成的数据需要再次审批。

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

**重要**：`current --check-heads` 必须退出 0。

**如果迁移失败**：不要降级、删除数据库卷或改写旧 revision；保留日志并按排障文档处理。

创建数据缓存卷并复制四个已通过预检的文件：

```bash
docker compose up --detach --wait data-agent
docker compose cp data/cache/demo/before_red.tif data-agent:/data/cache/before_red.tif
docker compose cp data/cache/demo/before_nir.tif data-agent:/data/cache/before_nir.tif
docker compose cp data/cache/demo/after_red.tif data-agent:/data/cache/after_red.tif
docker compose cp data/cache/demo/after_nir.tif data-agent:/data/cache/after_nir.tif
```

**注意**：
- 这一步只需要在 `data-cache` 卷为空或已被明确重建时执行
- 重复复制批准文件是安全的，但不能复制任意用户文件或改变卷内文件名

## 6. 真实上游验证

在完整演示前分别验证上游服务。命令只输出脱敏证据，不输出 Key、原始提示或原始响应。

### 大模型验证

```bash
docker compose run --rm --no-deps master-agent \
  python -m hennongxi_master.llm_smoke
```

**成功标准**：退出码 0 且 JSON 中 `ok=true`、`status="SUCCEEDED"`

**可以记录**：`task_id`、`plan_id`、`duration_ms`、模型名、token 数和两个 SHA-256

**不要记录**：`.env` 内容、完整的原始响应

**退出码含义**：

| 退出码 | 含义 |
| ---: | --- |
| 0 | 真实调用和计划校验通过 |
| 1 | 供应商调用或计划验证失败，查看脱敏 `error_code` |
| 2 | 配置缺失 |
| 3 | 验证程序内部错误 |

### 高德 Web 服务研究区校验（可选）

```bash
docker compose run --rm --no-deps master-agent \
  python -m hennongxi_master.amap_smoke
```

**成功标准**：退出码 0 且 JSON 中 `ok=true`、`code="VERIFIED"`、`infocode="10000"`

**输出内容**：只保留固定服务来源哈希、检查时间、耗时和匹配数量

**退出码含义**：
- 退出码 1 表示供应商返回了非通过状态（但这是安全的）
- 退出码 2 表示未配置
- 退出码 3 表示内部错误

**重要提醒**：高德失败不等于遥感主链不可用。

**关于额度**：验证会产生真实 API 调用和额度消耗，只在配置/轮换后或正式演练前执行，不要在自动化循环中运行。

### 高德浏览器地图验证（可选）

配置或轮换 `VITE_AMAP_JS_API_KEY` / `AMAP_JS_API_SECURITY_CODE` 后重建 Web：

```bash
docker compose up --detach --wait --force-recreate web
```

打开 [http://localhost:3000](http://localhost:3000)，进行以下检查：

**无任务时**：
- 确认普通道路地图可见
- 官方 Logo/版权可见
- 显示”高德位置参考”

**创建任务后**：
- 地图实例应保持
- 状态文案显示任务短 ID

**成果到达后**：
- 页面应销毁高德实例
- 切换为 MapLibre 的前期 NDVI、后期 NDVI 和 NDVI 差值图层

**记录内容**：
- 检查时间
- 加载状态
- 耗时
- 脱敏错误码（如有）
- 访问域名集合

**不得保存**：Key、安全密钥、原始响应、转换坐标或高德地图截图

**降级测试**：
随后在不投屏时把两个浏览器地图变量暂时置空并重建 Web。页面应直接显示”地图已就位”的离线占位图，不发出高德请求，任务创建仍可用。恢复变量后再次重建 Web。

**重要提醒**：真实检查会消耗 JS API 额度。

## 7. 启动完整系统

```bash
docker compose up --detach --wait
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/config/readiness
```

**预期结果**：

1. **服务状态**：`docker compose ps` 中八个常驻服务均为运行/健康状态
2. **聚合健康**：响应的 `state` 为 `HEALTHY`
3. **就绪检查**：响应为 `ready: true`、`llm_configured: true`、`data_configured: true`、`blockers: []`
4. **Web 页面**：[http://localhost:3000](http://localhost:3000) 显示”系统已就绪”
5. **地图显示**：
   - 配置独立 Web 端凭据时显示高德地图背景参考
   - 否则显示可用的离线占位图
6. **端口绑定**：宿主机只使用 `127.0.0.1:3000`、`:8000`、`:8004`

**关于就绪接口**：
就绪接口验证的是 Master 配置存在、数据清单可读和运行依赖健康，但它：
- 不会替代真实大模型、高德 Web 服务或浏览器地图验证
- 不会提前扫描命名卷中的四个大栅格
- 高德 JS API 未配置不会成为就绪 blocker

因此数据预检和至少一次真实任务仍然必需。

## 8. 日常启动、升级与停止

**已经完成初始化的机器日常启动**：

```bash
docker compose up --detach --wait
```

**拉取包含新迁移的代码后，先执行**：

```bash
docker compose up --detach --wait postgis redis
docker compose run --rm --no-deps master-agent alembic upgrade head
docker compose up --detach --wait
```

**非破坏性停止**：

```bash
docker compose down
```

**严禁在演示机器上执行以下命令**，除非项目所有者明确决定销毁全部本地状态且已有可验证备份：

```text
docker compose down -v
docker compose down --volumes
docker volume prune
```

这些操作会删除任务历史、数据缓存和生成成果。**普通停止不需要删除任何卷**。

## 9. 安装完成检查表

- [ ] Git 工作区干净，`.env` 被忽略且未被跟踪
- [ ] 四个真实栅格在本地预检中全部通过
- [ ] PostGIS 已迁移到 head，四个栅格已复制到 `data-cache` 卷
- [ ] 八个常驻服务健康，聚合健康与配置就绪均通过
- [ ] 真实大模型验证通过并只记录脱敏证据
- [ ] 如展示在线研究区校验，高德 Web 服务验证通过且已确认当日额度/状态
- [ ] 如展示高德地图，独立 Web 端 Key/安全密钥在线检查通过，且组件/隔离 E2E 的 5 秒网络失败回退保持通过；未保存地图截图
- [ ] 浏览器能访问 Web，控制台没有错误，未通过公网暴露端口
- [ ] 已阅读演示手册和排障文档，知道如何非破坏性停止
