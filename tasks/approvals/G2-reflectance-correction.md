# G2 变更审批：修正 2024-08-12 影像反射率重复偏移

## 审批状态

- 门禁：G2（权威演示数据）
- 状态：待审批
- 提交日期：2026-07-20
- 审批口令：`批准 G2 反射率修正`

## 变更原因

真实 Compose 私网链路已经生成五项 Analysis 成果，但 Quality Agent 对其中两项判定失败：

- `NDVI_AFTER` 的值超出 `[-1, 1]`；
- `NDVI_DIFFERENCE` 的值超出 `[-2, 2]`。

只读诊断确认，2024-08-12 场景的官方 Earth Search 项目元数据包含
`earthsearch:boa_offset_applied=true`。本地原始 COG 中，流域内通过 SCL 掩膜的植被红光
像元中位数为 283，对应已经校正后的反射率 `0.0283`；现有缓存脚本又执行了一次
`DN × 0.0001 - 0.1`，使 95.49% 的有效红光像元变成负值。这是重复应用偏移量，
不是 Quality Agent 阈值过严。

证据来源：

- [Element 84 Earth Search 官方说明](https://github.com/Element84/earth-search#gainoffset-in-items-after-jan-25-2022)
- [本次使用的官方 Earth Search 项目元数据](https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/S2A_49RDQ_20240812_0_L2A)
- 本地只读核验：COG 的 nodata 为 0；SCL 掩膜后流域有效覆盖率为 97.10%；红光原始值中位数为 283，近红外原始值中位数为 3709。

## 拟批准的变更

仅修正 2024-08-12 两个缓存波段的有效归一化公式：

- 现状：`反射率 = COG_DN × 0.0001 - 0.1`
- 修正后：`反射率 = COG_DN × 0.0001`

原因是该 COG 已经应用 BOA 偏移，不能再次减去 0.1。缓存生成器将显式记录
“源 COG 已应用 BOA 偏移”，并增加测试，阻止今后再次出现重复偏移。

## 不变范围

以下已批准内容保持不变：

- 后时相日期仍为 `2024-08-12`；
- Earth Search 项目、产品 ID、B04、B08、SCL 源文件及其 SHA-256 均不变；
- 流域边界、前时相 `2019-08-19` 及其缓存不变；
- 最低流域覆盖率 `0.95` 和最低有效像元比例 `0.90` 不变；
- 不重新下载影像，使用本地已有的不可变源文件离线重建。

## 批准后会改变的文件与数据

- `scripts/cache_demo_data.py`：区分源资产声明偏移与 COG 已应用偏移；
- `tests/test_cache_demo_data.py`：增加防重复偏移测试；
- `data/manifest.json`：更新 `after_red`、`after_nir` 的派生说明、文件大小和 SHA-256；
- `data/cache/demo/after_red.tif`、`after_nir.tif`：离线重建；这两个大文件继续被 Git 忽略。

## 批准后的验收条件

- 数据预检和全部缓存生成测试通过；
- 后时相 NDVI 全部有效值位于 `[-1, 1]`；
- NDVI 差值全部有效值位于 `[-2, 2]`；
- 真实 Data → Analysis → Quality 私网测试得到五项完整成果；
- 流域覆盖率不低于 0.95，有效像元比例不低于 0.90，质量结论为 `PASS`；
- 不降低或绕过任何 Quality Agent 阈值。

## 审批决定

请回复 `批准 G2 反射率修正` 后实施。若不批准，现有 G2 数据保持不变，T11 将继续诚实地判定真实链路为 `FAIL`。
