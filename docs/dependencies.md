# 依赖与运行时基线

本页记录 T01 的可复现依赖基线。核验日期为 2026-07-19；应用依赖全部使用精确版本，安装结果由 `uv.lock` 和 `apps/web/pnpm-lock.yaml` 固定。不得在演示前临时升级版本。

## 运行时与容器基线

| 组件 | 固定版本/镜像 | Apple Silicon 结论 | 选择理由 |
| --- | --- | --- | --- |
| Python | `3.12.13` / `python:3.12.13-slim-bookworm` | 官方镜像含 `linux/arm64` | 与规格一致，并避免开发机 Python 3.14 改变解析结果 |
| Node.js | `24.18.0` / `node:24.18.0-bookworm-slim` | 官方镜像含 `linux/arm64` | Node 24 为 LTS；前端和构建环境固定同一主版本 |
| uv | `0.11.29` | Python 通用安装方式可用 | 单一跨平台 `uv.lock`，后端使用 `uv sync --frozen` |
| pnpm | `11.9.0` | 随 Node 运行 | 由 `packageManager` 和锁文件共同固定 |
| PostgreSQL | `postgres:17.10-bookworm` | 官方镜像含 `linux/arm64` | T04 将以该镜像安装 Debian 官方仓库中的 PostGIS 包 |
| PostGIS | `3.6.4+dfsg-2.pgdg12+1` Debian 12/PG17 包 | PGDG 提供 `arm64` 包 | 从 PostgreSQL 官方 ARM64 基础镜像构建并执行扩展初始化 |
| Redis | `redis:8.8.0-alpine` | 官方镜像含 `linux/arm64` | 仅作事件/缓存传输，不承担最终事实源 |

官方 `postgis/postgis` 镜像目前只声明 `amd64`，不能满足规格中的原生 `linux/arm64` 验收。因此 T04 不使用平台模拟，也不静默固定 `linux/amd64`；优先从官方 PostgreSQL ARM64 基础镜像构建 PostGIS，并以真实的 `CREATE EXTENSION postgis` 和空间查询作为验收。若 Debian 包与目标架构不兼容，T04 必须停在失败状态并记录证据，不能把容器启动等同于 PostGIS 可用。

## 后端依赖

生产依赖按职责分组如下：

- API、配置与网络：FastAPI `0.139.2`、Pydantic `2.13.4`、pydantic-settings `2.14.2`、Uvicorn `0.51.0`、HTTPX `0.28.1`。
- 持久化与事件：SQLAlchemy `2.0.51`、Alembic `1.18.5`、asyncpg `0.31.0`、redis-py `8.0.1`。
- GIS 与报告：NumPy `2.5.1`、Rasterio `1.5.0`、GeoPandas `1.1.4`、Shapely `2.1.2`、Rio-Tiler `9.4.0`、ReportLab `5.0.0`。
- 可观测性：structlog `26.1.0`。
- 开发质量门：pytest `9.1.1`、pytest-asyncio `1.4.0`、pytest-cov `7.1.0`、RESPX `0.22.0`、OpenAPI Spec Validator `0.7.2`、PyYAML `6.0.3`/types-PyYAML `6.0.12.20260518`、Ruff `0.15.22`、mypy `2.3.0`。

Rasterio `1.5.0` 要求 Python 3.12、NumPy 2 和 GDAL 3.8 以上；本地锁文件验证 Python 层解析，T04 再用目标容器验证系统动态库和真实 raster I/O。

## 前端依赖

- 运行时：React/React DOM `19.2.7`、MapLibre GL JS `5.24.0`。
- 构建与测试：Vite `8.1.0`、React Vite 插件 `6.0.3`、Vitest `4.1.10`、jsdom `29.1.1`、Testing Library React `16.3.2`。
- 类型与 lint：TypeScript `6.0.3`、ESLint `10.7.0`（`@eslint/js` `10.0.1`）、typescript-eslint `8.64.0`。

TypeScript 没有采用刚发布的 7.x：这个 10 天演示优先选择已被当前 React 类型和 typescript-eslint 支持的 6.0 补丁版。MapLibre 固定稳定的 5.x，不采用仍处于预发布状态的 6.x。

## 可复现安装与检查

后端（需要 Python 3.12）：

```bash
python3 -m pip install uv==0.11.29
uv sync --frozen --all-groups
uv run python -c "import fastapi, geopandas, rasterio, rio_tiler; print('backend dependencies ok')"
uv run ruff --version
uv run mypy --version
```

前端（需要 Node 24）：

```bash
corepack enable
corepack prepare pnpm@11.9.0 --activate
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web exec vite --version
pnpm --dir apps/web exec vitest --version
```

锁文件更新是显式维护动作：先修改精确版本，再运行 `uv lock` 或 `pnpm --dir apps/web install --lockfile-only`，检查差异并重新执行上述冻结安装。CI 与 Dockerfile 禁止无锁安装。

## 配置与秘密

`.env.example` 只包含公开端点和本地开发占位值。真实 `.env`、LLM 密钥、私有 base URL、原始影像、缓存和生成物均由 `.gitignore` 排除。日志、异常、数据库记录和 API 响应不得包含 `LLM_API_KEY`、Authorization 头或完整供应商响应。

真实 LLM 配置缺失时，服务可运行确定性测试，但 readiness 必须明确报告该外部门未满足；不得把内置计划伪装成真实模型结果。

## 许可证与字体约束

- 当前 Python/JavaScript 库主要采用 MIT、BSD-3-Clause、Apache-2.0 或 PostgreSQL 类许可证；PostGIS 自身使用 GPLv2-or-later，并作为独立数据库扩展运行。发布镜像前仍要从锁文件生成第三方许可证清单。
- T13 必须选择允许再分发和嵌入 PDF 的中文字体，同时在仓库记录字体名称、版本、来源 URL、许可证文本和 SHA-256；字体选择完成前不得把系统字体复制进镜像或仓库。
- 原始 GIS 数据的来源和许可不属于依赖锁；由 T05 的数据清单和 G2 审批单独约束。

## 官方来源

- [Python 3.12.13](https://www.python.org/downloads/release/python-31213/) 与 [Python 官方镜像](https://hub.docker.com/_/python)
- [Node.js 发布状态](https://nodejs.org/en/about/previous-releases) 与 [Node 官方镜像](https://hub.docker.com/_/node)
- [uv 锁文件文档](https://docs.astral.sh/uv/concepts/projects/layout/#the-lockfile)
- [FastAPI 发布说明](https://fastapi.tiangolo.com/release-notes/)、[Pydantic 发布说明](https://docs.pydantic.dev/latest/changelog/) 与 [Rasterio 安装说明](https://rasterio.readthedocs.io/en/stable/installation.html)
- [PostgreSQL 官方镜像](https://hub.docker.com/_/postgres)、[PostGIS 安装说明](https://postgis.net/documentation/getting_started/install_ubuntu/)、[PGDG PostGIS 包](https://apt.postgresql.org/pub/repos/apt/pool/main/p/postgis/) 与 [postgis/postgis 架构声明](https://github.com/postgis/docker-postgis)
- [Redis 官方镜像](https://hub.docker.com/_/redis)
- [React 19.2](https://react.dev/blog/2025/10/01/react-19-2)、[Vite 发布记录](https://vite.dev/blog)、[TypeScript 发布说明](https://www.typescriptlang.org/docs/handbook/release-notes/overview.html) 与 [MapLibre GL JS](https://www.npmjs.com/package/maplibre-gl?activeTab=versions)
