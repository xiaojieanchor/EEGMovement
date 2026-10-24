# EEGMovement 归档入口

2026-10-22：全部已归档实体已集中移入G盘，旧模型与训练体系继续停用。C盘项目只留下本说明及归档入口文档。

**归档根目录：** `G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/`

[打开完整归档说明](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/README.md)

| 内容 | G盘位置 |
|---|---|
| 旧设备、模型、训练脚本/逻辑、配置、数据与结果 | `retired/` |
| G盘原模型库 | `retired-models/` |
| 两份完整ZIP快照 | `legacy-project-and-runtime-baseline.zip`、`legacy-model-library.zip` |
| 全文件清单与SHA256 | [asset-index.csv](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/asset-index.csv) |
| 文档标签 | [document-labels.csv](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/document-labels.csv) |
| 原路径与归档位置映射 | [relocation-map.json](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/relocation-map.json) |
| 本次迁盘清单与核验凭据 | [迁移凭据](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/migration-20261022/receipt.json) |
| G盘独立重读核验 | [7,302个文件SHA256核验通过](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/migration-20261022/verification-latest.json) |

本次从C盘迁入7,302个归档文件，来源共5,826,959,186字节；逐文件SHA256核对后移除C盘归档载荷。G盘原有模型归档保留，重复同内容文件核对复用，冲突版本另存。旧报告原样保留，当前索引已经更新为G盘位置。

保留在当前项目的是VR工程、连接/配对、中枢、控制核心及必要运行兼容模块；新设备的`g-tec/`、`programfile_gtec/`及g.Nautilus说明书仍是待接入资料，未作为旧设备归档移走。未启动训练或模型服务，未操作服务器。归档不代表新设备已接入；旧录制关闭状态、数据角色与历史结论不改变。

G盘不在线时不能读取归档实体，当前项目不会自动恢复旧模型。恢复历史文件时先解压到新的空目录并核对SHA，不要整库覆盖当前工程。
