# Publisher 中文字体来源

T13 使用 `NotoSansSC-VF.ttf` 作为 PDF 内嵌字体。该文件是 Noto CJK 官方仓库提供的
简体中文区域子集可变 TrueType 字体，未做修改。

- 上游仓库：`https://github.com/notofonts/noto-cjk`
- 固定提交：`f8d157532fbfaeda587e826d4cd5b21a49186f7c`
- 上游文件：`Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf`
- 本地字节数：`17,773,132`
- SHA-256：`d68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964`
- 许可证：SIL Open Font License 1.1，原文保存在同目录 `LICENSE.txt`
- 许可证 SHA-256：`6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2`

选择子集 TTF 是为了同时满足简体中文字符覆盖、ReportLab TrueType 内嵌和离线容器运行。
字体及许可证必须一起保留；不得用宿主机字体或运行时网络下载替代。
