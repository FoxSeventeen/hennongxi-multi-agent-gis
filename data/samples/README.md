# T05 权威数据审批记录（G2 变更已批准）

本文记录 2026-07-19 首次通过 G2 审批的数据源，以及真实像元检查后提出的替换方案。
首次审批的后期影像没有达到质量门槛，因此将其拒绝为最终分析数据源；2024-08-12
替换方案已经获得审批，最终缓存与预检现已完成。

## 已批准的变更

- [x] 已批准将后期影像从 `S2A_49RDQ_20240822_0_L2A` 替换为
  `S2A_49RDQ_20240812_0_L2A`。
- 保持不变：神农溪流域边界、2019-08-19 前期影像、Sentinel-2A 平台、MGRS 49RDQ
  瓦片、B04/B08 波段、SCL 掩膜类别、比例/偏移、许可和质量门槛。
- 变更原因：原后期影像的流域内有效像元比例为 88.61%，低于 90%；替换候选的 SCL
  预检有效像元比例为 97.10%。

## 流域边界

- 数据生产方：WWF HydroSHEDS / HydroBASINS。
- 产品：HydroBASINS 亚洲标准版，第 12 级，版本 1c（`hybas_as_lev12_v1c`）。
- 官方压缩包：[hybas_as_lev12_v1c.zip][hydrobasins-archive]。
- 压缩包大小：80,155,135 字节。
- 压缩包 SHA-256：
  `05e98a001fc526cd5fcdbbc8144fe0aa3ce6712c35624f72e4728b219193fdb9`。
- 许可：[HydroBASINS 产品许可][hydrobasins-product]允许科研、教育和商业使用；署名、
  再分发等条件以 HydroSHEDS 技术文档中的许可条款为准。
- 派生方法：合并出口单元 `HYBAS_ID=4120733210` 和所有通过 `NEXT_DOWN` 最终流向该
  出口的第 12 级单元，共选择 8 个源多边形；排除下游长江单元
  `HYBAS_ID=4120733220`。
- 结果：1 个有效的 EPSG:4326 多边形，范围为
  `(110.108333, 31.045833, 110.537500, 31.466667)`；HydroBASINS 上游面积为
  1,057.1 km²，等积投影测量面积为 1,056.4 km²。
- 已批准成果：`data/boundaries/shennongxi_watershed.geojson`，属性为
  `approval_status=approved`。

边界可通过以下命令重复生成：

```bash
python scripts/derive_watershed.py
```

## 首次审批的影像组合

影像为 ESA/欧盟生产的 Copernicus Sentinel-2 Level-2A 地表反射率数据，由 Element 84
的 [Earth Search][earth-search] 以无需登录的公开云优化 GeoTIFF（COG）形式分发。
两期均使用 Sentinel-2A、相同 MGRS 瓦片和轨道，以及相同的 10 m 红光/近红外网格。
[Copernicus Sentinel 法律声明][sentinel-license]允许免费、完整、开放使用；派生成果需
注明其包含经过修改的 Copernicus Sentinel 数据。

| 用途 | STAC 条目 / SAFE 产品 | 获取时间 | 瓦片 | 整景云量 | 处理基线 |
| --- | --- | --- | --- | ---: | --- |
| 前期 | `S2A_49RDQ_20190819_0_L2A` / `S2A_MSIL2A_20190819T031541_N0213_R118_T49RDQ_20190819T072545.SAFE` | 2019-08-19 03:29:27Z | MGRS 49RDQ | 7.617735% | 02.13 |
| 原后期（已拒绝） | `S2A_49RDQ_20240822_0_L2A` / `S2A_MSIL2A_20240822T031541_N0511_R118_T49RDQ_20240822T082151.SAFE` | 2024-08-22 03:29:22Z | MGRS 49RDQ | 9.908610% | 05.11 |

首次组合相隔 5 年零 3 个日历日，两个瓦片范围均完整包含流域边界；但范围覆盖不代表
云和阴影掩膜后的有效像元一定达标。

### 首次审批的精确源文件

| 日期 | 文件 | 分辨率 | 字节数 | 不可变 ETag |
| --- | --- | ---: | ---: | --- |
| 2019-08-19 | [B04 红光][before-red] | 10 m | 213,701,170 | `"392e85607586145aebe1788773902e85-26"` |
| 2019-08-19 | [B08 近红外][before-nir] | 10 m | 251,428,986 | `"f42030d96e21d78229197a33f01e945c-30"` |
| 2019-08-19 | [SCL 质量掩膜][before-scl] | 20 m | 2,794,209 | `"14650eb42dd4e0541d9e8e764852f3dc"` |
| 2024-08-22 | [B04 红光][after-red] | 10 m | 213,717,292 | `"7c7c228bc55d5bc6a18c7d58049ea4c0-26"` |
| 2024-08-22 | [B08 近红外][after-nir] | 10 m | 253,069,802 | `"3122fd7306b65d66ffd673064b360898-31"` |
| 2024-08-22 | [SCL 质量掩膜][after-scl] | 20 m | 1,849,243 | `"2e15039872b4829fb98cc48a8a8ba25c"` |

上述 6 个 URL 均无需凭据即可返回 HTTP 200，并支持不可变缓存和字节范围请求。B04 映射
到红光逻辑输入，B08 映射到近红外逻辑输入；SCL 只是质量辅助源，不会成为第 6 个应用
逻辑输入。

## 已批准的数据转换规则

缓存命令先校验并保存获批源文件，再只裁剪共同的流域窗口，保留 EPSG:32649、10 m
共享网格，并写出 4 个本地 Float32 GeoTIFF 输入。转换规则如下：

- 2019 年反射率：`DN * 0.0001 + 0.0`。
- 2024 年反射率：`DN * 0.0001 - 0.1`。
- SCL 使用最近邻方法重采样到 10 m。
- 流域外像元及 SCL 类别 0、1、3、8、9、10、11 写为 `-9999` nodata。
- 最终 `data/manifest.json` 记录源标识、转换规则、本地文件大小和 SHA-256。

验收门槛保持为：每个逻辑输入至少覆盖 95% 流域，且流域内有效像元比例至少为 90%。
任一日期未达标时，T05 不得完成，也不得通过降低门槛绕过审批。

## 真实像元检查结果

首次缓存运行中，4 个输入的几何覆盖率均为 100%，且网格完全对齐：

| 日期 | 红光有效像元 | 近红外有效像元 | 结论 |
| --- | ---: | ---: | --- |
| 2019-08-19 | 95.74% | 95.74% | 通过 |
| 2024-08-22 | 88.61% | 88.61% | 不通过，拒绝作为最终后期影像 |

## 获批替换的后期影像

- STAC 条目：`S2A_49RDQ_20240812_0_L2A`。
- SAFE 产品：`S2A_MSIL2A_20240812T031521_N0511_R118_T49RDQ_20240812T084251.SAFE`。
- 获取时间：2024-08-12 03:29:26Z。
- 平台与网格：Sentinel-2A，MGRS 49RDQ，处理基线 05.11。
- 整景云量：6.090892%。
- SCL 流域预检：10,556,343 个流域像元中，10,250,509 个有效，有效率 97.10%。
- 与前期影像的日历日期相差 7 天；平台、瓦片、轨道、分辨率、比例/偏移、掩膜和许可
  均保持不变。

### 获批替换的精确源文件

| 文件 | 分辨率 | 字节数 | 不可变 ETag | SHA-256 |
| --- | ---: | ---: | --- | --- |
| [2024-08-12 B04 红光][replacement-red] | 10 m | 209,096,458 | `"a55dd5415755e8cbe5d6ae47a9b52a93-25"` | `a0ea3cbf8fef6bfa0387acee7e8e5e4658eac30656ab725d97af3adaa255c9a0` |
| [2024-08-12 B08 近红外][replacement-nir] | 10 m | 253,879,149 | `"1cca7fe777e92138567e5d874719d29c-31"` | `d5e1f58bd91e7540b3ca1a327fafad13540735d098ef87cde1319a236a070a76` |
| [2024-08-12 SCL 质量掩膜][replacement-scl] | 20 m | 1,438,732 | `"85926f6837db5c1a67359c4379f4794c"` | `c42afb6eb470a7dff985a4aedbfeab25809445d91fa8e6f619bf4b29effaf8d0` |

## 最终离线成果

`python scripts/cache_demo_data.py` 已生成并验证以下成果；源影像和四个派生 GeoTIFF
位于被 Git 忽略的本地缓存中，`data/manifest.json` 记录其完整来源与转换链。

| 逻辑数据 | 本地文件 | 字节数 | SHA-256 | 流域覆盖率 | 有效像元比例 |
| --- | --- | ---: | --- | ---: | ---: |
| 流域 | `data/boundaries/shennongxi_watershed.geojson` | 9,099 | `1c44b253e6220364109d6a62b17d2a66ef19ef12a6f4dc368ba1a41142eed7c3` | — | — |
| 前期红光 | `data/cache/demo/before_red.tif` | 42,342,174 | `815552ea48aca8610b62544b85be8b006e4d25252a3c4fe083853824bf66499f` | 100% | 95.74% |
| 前期近红外 | `data/cache/demo/before_nir.tif` | 41,472,540 | `d15c4b665c3ea33347dfe78c5b3514e38c37859429c8d488cba07193299ce0c1` | 100% | 95.74% |
| 后期红光 | `data/cache/demo/after_red.tif` | 40,639,174 | `66bc7e4d207b714da252a76902ea39747df9a6539aabaacf73f1fe2ff741ddc2` | 100% | 97.10% |
| 后期近红外 | `data/cache/demo/after_nir.tif` | 42,175,838 | `275d062c884c8ef8c6fdd2b3f63f295ef94675c6888f6db81de3fd1ea566ffe2` | 100% | 97.10% |

四个 GeoTIFF 均为 EPSG:32649、10 m、Float32、nodata `-9999`，且使用完全一致的像元
网格。再次运行缓存命令时会在无网络环境中返回 `Reused verified offline cache`，表示已复用
验证通过的离线缓存。

## 审批记录

- 2026-07-19：首次 G2 来源组合获批。
- 2026-07-19：真实像元检查拒绝 2024-08-22 后期影像。
- 2026-07-19：用户回复 `批准 G2 变更：after 改为 2024-08-12`，替换方案获批。

[hydrobasins-product]: https://www.hydrosheds.org/products/hydrobasins
[hydrobasins-archive]: https://data.hydrosheds.org/file/hydrobasins/standard/hybas_as_lev12_v1c.zip
[earth-search]: https://github.com/Element84/earth-search
[sentinel-license]: https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice
[before-red]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2019/8/S2A_49RDQ_20190819_0_L2A/B04.tif
[before-nir]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2019/8/S2A_49RDQ_20190819_0_L2A/B08.tif
[before-scl]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2019/8/S2A_49RDQ_20190819_0_L2A/SCL.tif
[after-red]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2024/8/S2A_49RDQ_20240822_0_L2A/B04.tif
[after-nir]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2024/8/S2A_49RDQ_20240822_0_L2A/B08.tif
[after-scl]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2024/8/S2A_49RDQ_20240822_0_L2A/SCL.tif
[replacement-red]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2024/8/S2A_49RDQ_20240812_0_L2A/B04.tif
[replacement-nir]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2024/8/S2A_49RDQ_20240812_0_L2A/B08.tif
[replacement-scl]: https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ/2024/8/S2A_49RDQ_20240812_0_L2A/SCL.tif
