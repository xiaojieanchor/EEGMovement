# EEGMovement

Windows g.Nautilus参与者采集端、本机中枢、私有计算服务、模型训练／登记／推理、Quest VR和公网转发的完整项目源码。

**当前目标：以第一人称行走／转向运动想象为主，眼动辅助；不绑定某只手或腿。** 公开数据方法结果不能代替个人VR控制效果。

- [1.1.8 采集到中枢链路验收、日志与打标存储](docs/采集到中枢链路验收_20261024.md)
- [本机采集端进度、连接诊断与原厂共享会话](docs/采集端开发进度与连接诊断_20261024.md)
- [另一台Windows的安装、采集端启动、测试与开发](docs/另一台Windows开发指南.md)
- [当前任务语义与32通道候选布局](docs/gnautilus/运动想象主线与眼动辅助_目标修正_20261024.md)
- [第三方许可与未包含材料](THIRD_PARTY_NOTICES.md)

## 源码结构

| 目录 | 内容 |
|---|---|
| `gnautilus_acquisition/` | 独立Windows采集工作台、波形、原厂SDK参与者读取、日志、保存与更新 |
| `hub/` | 原有网页、中枢接收、主动打标、状态、模型库与执行检查 |
| `gnautilus_ml/` | 数据合同、治理、持久任务、分类器／深度模型、模型登记、推理与SSH桥接 |
| `gnautilus_lab/` | 方法参考、信号和决策模块 |
| `eeg_relay/`, `deploy/` | 公网转发、有限缓存、注册、签名更新和服务配置 |
| `unity/EEGMovement/` | Quest／桌面VR Unity工程源码、场景、Packages和项目设置 |
| `receiver/`, `protocols/` | C#接收端、STOP／租约验证及通信协议 |
| `scripts/`, `tests/` | 启动、构建、下载、训练、诊断、发布工具与测试 |
| `docs/`, `configs/` | 项目文档、非秘密配置与本地配置模板 |

公开版本不包含凭据／私钥、运行配对状态、个人EEG、公共数据库本体、模型权重、安装包和缓存；也不上传原厂专有软件或禁止再分发的角色素材。本地原内容保留。采集端的公开CA与更新验证公钥随源码提供，身份仍按既有自动注册流程生成，无需人工复制配对文件。

首次开发请按上方指南安装依赖。初始不会启动训练、模型推理或VR动作。`training/`仅为运行兼容模块，旧设备模型与训练器仍归档，不恢复。

---

以下为设备切换时的历史入口说明，路径和“未配置”等状态以当前任务说明及实际接口为准。


当前阶段：**g.tec g.Nautilus数据链软件已实现，真实设备与现场网络待验收**（2026-10-22）。

全部归档实体已集中至G盘；位置与内容见[归档说明](归档说明.md)。本地`archives/`只保留说明文档。

项目目标不变：在VR中实时检测EEG运动意图、驱动对应模型，并提供实时视觉反馈。后续模型结构与训练方法尚未选定。

## 当前入口

| 资料 | 入口 |
|---|---|
| 新设备原始文件与登记 | [g.Nautilus资料](devices/gnautilus/README.md) |
| 旧设备、模型、训练逻辑、数据与结果 | [完整归档](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/README.md) |
| 归档分类与逐文档标签 | [文档标签CSV](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/document-labels.csv) |
| 当前数据阶段与历史索引 | [数据入口](data/README.md) |
| 切换阶段配置 | [device_transition.json](configs/device_transition.json) |
| VR网络与Quest说明 | [Quest连接](docs/quest-lan.md) |
| 独立采集端、最新构建与验收 | [本轮交付总览](docs/gnautilus/integration_20261022/交付总览.md) |
| Python API、操作及故障定位 | [接入说明](docs/gnautilus/integration_20261022/Python%20API与独立采集端接入说明_20261022.md) |

## 保留的系统

`hub/`保留中枢、会话、反馈及控制接口；`unity/`、`receiver/`与`protocols/`保留VR工程、执行接收器和通信协议。VR配对材料、签名、时效、失联停止、急停和执行回执逻辑继续保留。`scripts/`保留相关启动、构建和联调工具。

旧模型、训练器、训练脚本、实验队列、数据派生和训练配置已经退出当前开发目录，移入归档的`retired/`。`training/`仅保留中枢必须使用的6个兼容模块，说明见[兼容依赖](training/README.md)，不包含可训练网络或训练入口。G盘旧模型库另行存入同次归档，具体位置与核验以归档报告为准。

## 切换期间的运行边界

g.Nautilus已提供独立Windows采集工作台、只读共享参与者、TLS传输、中枢动态通道接收、主动标签与持久化。工作台包含原始波形、连接分层测试和中枢状态日志。原厂配置与设备启停仍由原厂窗口负责；真实共享能力、EEG与现场网络尚未验收。新模型尚未配置。

中枢默认使用新设备独立入口，暂停旧设备采集，不启动旧模型推理或训练后台。旧七槽、250Hz、CH1/CH6故障规则和旧模型成绩不适用于新设备。停止、急停、Quest连接与执行回执继续保留；新模型后续单独确定和验收。

原README及旧阶段说明已完整保存到[历史入口文档](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/historical-entrypoints/README.before-transition.md)与ZIP快照。原数据、协议和历史失败记录均保留原字节；移动映射记录原路径。服务器独有资料尚未远程归档，本次未连接、停止或修改远端任务。

采集端最新合并与实测：[1.1.11 连续传输验证](docs/gnautilus/采集端合并与连续传输验证_1.1.11_20261024.md)。
