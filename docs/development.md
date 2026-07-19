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
