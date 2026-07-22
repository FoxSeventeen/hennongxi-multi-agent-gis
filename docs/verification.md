# T28 目标机验收与证据模板

本文既是验收清单，也是填写说明。仓库中的模板不能证明真实演练已经发生；接手人应复制一份到
Git 忽略的 `tmp/t28/`，填写真实结果并让另一位复核人签字。不得在记录中粘贴 `.env`、Key、原始
模型响应、高德原始响应、完整日志或含敏感信息的供应商控制台截图。

建议创建本地副本：

```bash
mkdir -p tmp/t28
cp docs/verification.md tmp/t28/verification-YYYYMMDD.md
git check-ignore -v tmp/t28/verification-YYYYMMDD.md
```

## 1. 演练基本信息

| 字段 | 实际值 |
| --- | --- |
| 日期与时区 | `<YYYY-MM-DD HH:MM Asia/Shanghai>` |
| 操作员 | `<姓名>` |
| 复核人 | `<姓名>` |
| Git 提交 | `<完整 SHA>` |
| Git 工作区 | `<clean / 仅列出预期文件>` |
| 目标机器 | `<型号；不要记录序列号>` |
| 架构 | `<arm64>` |
| macOS | `<版本>` |
| OrbStack/Docker | `<产品与版本>` |
| Docker Compose | `<版本>` |
| 浏览器 | `<名称与版本>` |
| 大模型 | `<模型名；不写 Key/Base URL>` |
| 高德 | `<启用/未启用；Key 已轮换：是/否>` |

## 2. 安装与静态检查

按顺序执行并填写实际结果：

| 检查 | 命令/动作 | 通过条件 | 实际结果 |
| --- | --- | --- | --- |
| 工作区 | `git status --short --branch` | 分支/提交正确，无非预期文件 | `<待填>` |
| 配置忽略 | `git check-ignore -v .env` | 命中 `.gitignore` | `<待填>` |
| Compose 解析 | `docker compose config --quiet` | 退出 0 | `<待填>` |
| 数据预检 | `uv run --frozen python scripts/data_preflight.py` | 19 项 `[PASS]` | `<待填>` |
| 数据日期 | 查看 `data/manifest.json` | 2019-08-19 / 2024-08-12 | `<待填>` |
| 数据边界 | 查看清单与审批 | HydroBASINS 流域，四栅格 SHA-256 匹配 | `<待填>` |
| 数据卷 | 按 `setup.md` 复制/复用 | 真实任务不报 `DATA_INVALID` | `<待填>` |
| 数据库迁移 | `alembic current --check-heads` | 退出 0 | `<待填>` |

数据预检证据只记录 PASS/FAIL、文件逻辑名、大小和清单中的 SHA-256，不复制外部私有下载 URL。

## 3. 运行健康与端口

```bash
docker compose up --detach --wait
docker compose ps
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/health
curl --fail --silent --show-error http://127.0.0.1:8000/api/v1/config/readiness
```

| 检查 | 通过条件 | 实际结果 |
| --- | --- | --- |
| 常驻服务 | Web、五 Agent、PostGIS、Redis 共八个服务运行/健康 | `<待填>` |
| 聚合健康 | `state=HEALTHY`，各依赖可用 | `<待填>` |
| 配置就绪 | `ready=true`、LLM/data 为 true、无 blocker | `<待填>` |
| 宿主端口 | 只使用回环地址 3000/8000/8004 | `<待填>` |
| 私有边界 | Data/Analysis/Quality/PostGIS/Redis 无宿主端口 | `<待填>` |
| 首页 | 显示“系统已就绪”，浏览器控制台无错误 | `<待填>` |

## 4. 真实上游证据

### 4.1 大模型冒烟

```bash
docker compose run --rm --no-deps master-agent \
  python -m hennongxi_master.llm_smoke
```

只填写脱敏字段：

| 字段 | 实际值 |
| --- | --- |
| `ok` / `status` | `<true / SUCCEEDED>` |
| `model` | `<模型名>` |
| `task_id` | `<UUID>` |
| `plan_id` | `<UUID>` |
| `started_at` | `<时间>` |
| `duration_ms` | `<整数>` |
| token 数 | `<input/output；供应商未返回则填 null>` |
| `provider_origin_sha256` | `<64 位哈希>` |
| `response_sha256` | `<64 位哈希>` |
| 固定步骤 | `<五个 step kind>` |

结论：`<通过/不通过>`。真实大模型未通过时不能签署最终验收。

### 4.2 高德冒烟（计划展示在线校验时）

```bash
docker compose run --rm --no-deps master-agent \
  python -m hennongxi_master.amap_smoke
```

| 字段 | 实际值 |
| --- | --- |
| `ok` / `code` | `<true / VERIFIED>` |
| `infocode` | `<10000>` |
| `checked_at` | `<时间>` |
| `duration_ms` | `<整数>` |
| `match_count` | `<整数>` |
| `provider_origin_sha256` | `<64 位哈希>` |
| 当日额度/控制台状态 | `<人工确认通过；不附 Key 截图>` |

高德未通过时记录脱敏 code/retryable，并按“可选增强降级”处理，不能修改 G2 数据边界来绕过。

## 5. 自动化基线

| 运行 | 准备 | 通过条件 | 实际结果 |
| --- | --- | --- | --- |
| 冷启动 E2E | 仅清空 `hennongxi-e2e` 专用 volumes | Playwright 4/4 | `<待填>` |
| 温缓存 E2E | 保留同一专用 volumes 再运行 | Playwright 4/4 | `<待填>` |

命令以 [`../tests/e2e/README.md`](../tests/e2e/README.md) 为准。E2E 使用假上游，因此这里只证明
浏览器与完整容器链，不计入真实 LLM/高德证据。

## 6. 真实 UI 主链记录

查询固定为：

> 分析巴东县神农溪 2019 至 2024 年植被变化

| 字段 | 实际值 |
| --- | --- |
| 任务 ID | `<UUID>` |
| attempt | `<1>` |
| correlation ID | `<UUID>` |
| 创建时间 | `<时间>` |
| 完成时间 | `<时间>` |
| 总耗时 | `<毫秒/秒>` |
| 终态 | `<COMPLETED>` |
| 规划来源 | `<LLM / 页面“真实大模型规划”>` |
| 位置校验 | `<VERIFIED/DEGRADED + 脱敏原因码>` |
| 五个阶段 | `<全部完成；分别记录耗时>` |
| 前后日期 | `<2019-08-19 / 2024-08-12>` |
| 质量结论 | `<PASS>` |
| PDF 下载 | `<HTTP 200、application/pdf、可打开>` |
| 页面刷新 | `<同一 task_id 完整恢复>` |

从 `GET /api/v1/tasks/{task_id}` 或页面记录每个完整成果的 `artifact_type`、`byte_size` 和
`checksum_sha256`。不要记录容器内路径。

| 成果 | 字节数 | SHA-256 | 复核 |
| --- | ---: | --- | --- |
| `NDVI_BEFORE` | `<待填>` | `<待填>` | `<通过/不通过>` |
| `NDVI_AFTER` | `<待填>` | `<待填>` | `<通过/不通过>` |
| `NDVI_DIFFERENCE` | `<待填>` | `<待填>` | `<通过/不通过>` |
| `CHANGE_CLASSIFICATION` | `<待填>` | `<待填>` | `<通过/不通过>` |
| `AREA_STATISTICS` | `<待填>` | `<待填>` | `<通过/不通过>` |
| `QUALITY_REPORT` | `<待填>` | `<待填>` | `<通过/不通过>` |
| `PDF_REPORT` | `<待填>` | `<待填>` | `<通过/不通过>` |

四项质量指标：

| 指标 | 实际值 | 门槛/结论 |
| --- | ---: | --- |
| 流域覆盖率 | `<待填>` | `<待填>` |
| 有效像元率 | `<待填>` | `<待填>` |
| 5/5 分析成果完整性 | `<待填>` | `<待填>` |
| Analysis 耗时 | `<待填>` | `<待填>` |

截图最少包含：首页就绪、同一 ID 的五 Agent 时间线、三类地图之一与完整流域、面积/质量、PDF
正文、刷新恢复。截图放在 `tmp/t28/` 或仓库外；检查其中没有 Key、供应商 URL 或个人信息。

## 7. 失败与重试记录

按 [`demo-runbook.md`](demo-runbook.md) 停止 Publisher 制造可恢复失败。

| 字段 | 实际值 |
| --- | --- |
| 任务 ID | `<UUID>` |
| attempt 1 失败阶段/Agent | `<PUBLISHING / publisher>` |
| 脱敏错误码 | `<待填>` |
| 失败时是否隐藏完整成果/PDF | `<是>` |
| Publisher 恢复健康 | `<时间与结果>` |
| retry 接受 | `<attempt 2 / PENDING>` |
| 安全复用步骤 | `<页面实际显示>` |
| attempt 1 证据是否仍可见 | `<是>` |
| attempt 2 终态 | `<COMPLETED>` |

## 8. 高德降级与离线记录

分别记录“Key 未配置”和“真实外网断开”，不要合并：

| 场景 | 位置状态 | 规划来源 | 终态 | 四栅格/统计/质量与在线一致 |
| --- | --- | --- | --- | --- |
| Key 置空 | `<DEGRADED>` | `<真实 LLM>` | `<COMPLETED>` | `<是>` |
| 主机外网断开 | `<DEGRADED>` | `<BUILTIN_RECOVERY 或实际值>` | `<COMPLETED>` | `<是>` |

外网恢复后再次执行健康检查、真实 LLM 冒烟和（启用时）高德冒烟，记录恢复时间。离线链不能替代
第 4 节的真实 LLM 成功证据。

## 9. 规格 12 项成功标准签署

| # | 成功标准 | 具名证据 | 复核 |
| ---: | --- | --- | --- |
| 1 | Compose 在目标机启动全部服务 | `<第 3 节>` | `<通过/不通过>` |
| 2 | UI 展示各 Agent 健康/网络状态 | `<截图编号>` | `<通过/不通过>` |
| 3 | 中文请求经真实 LLM 生成有效计划 | `<冒烟 + 真实任务 ID>` | `<通过/不通过>` |
| 4 | 同一 task_id 贯穿时间线与持久化记录 | `<任务 ID + 刷新证据>` | `<通过/不通过>` |
| 5 | 真实红/NIR 计算两期 NDVI 与差值 | `<成果 SHA-256>` | `<通过/不通过>` |
| 6 | 地图显示完整流域和计算结果 | `<截图编号>` | `<通过/不通过>` |
| 7 | Quality 展示四项质量证据 | `<指标表 + 截图>` | `<通过/不通过>` |
| 8 | Publisher 生成同任务中文 PDF | `<PDF 校验和 + 截图>` | `<通过/不通过>` |
| 9 | UI 自动完成且刷新后恢复 | `<刷新记录>` | `<通过/不通过>` |
| 10 | 强制 Agent 失败并从 UI 重试 | `<第 7 节>` | `<通过/不通过>` |
| 11 | 单元/契约/集成/浏览器测试通过 | `<T27 证据 + 本机 E2E>` | `<通过/不通过>` |
| 12 | 使用缓存演练且现场不下载影像 | `<数据预检 + 网络记录>` | `<通过/不通过>` |

## 10. 安全与停止复核

- [ ] `.env`、所有 Key 和供应商原始响应未进入 Git、截图、日志摘录或验收记录。
- [ ] 原始/缓存影像、生成成果、PDF 和测试产物未进入 Git。
- [ ] 高德没有替代 WGS84 流域、Sentinel-2 影像或批准日期。
- [ ] 只使用 `docker compose down` 非破坏性停止，命名卷仍存在。
- [ ] `git status --short` 只含演示前已有的预期文件。
- [ ] 高德 Key 在交付/曝光后已轮换，真实大模型 Key 的保管人明确。

## 11. 最终结论

| 角色 | 姓名 | 结论 | 时间 |
| --- | --- | --- | --- |
| 操作员 | `<待填>` | `<通过/不通过；阻塞项>` | `<待填>` |
| 复核人 | `<待填>` | `<通过/不通过；阻塞项>` | `<待填>` |

只有第 9 节 12 项全部有真实具名证据、第 10 节全部勾选且没有未关闭阻断项，才能把 T28 和最终
检查点标为完成。
