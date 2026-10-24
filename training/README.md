# 运行兼容模块（非训练目录）

2026-10-22：旧训练器、网络定义、模型、实验队列和训练逻辑已移至 [完整历史training](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/retired/training/)。本目录只为保留中枢/控制链路的现有导入路径提供6个原样兼容模块：

| 文件 | 保留原因 |
|---|---|
| `__init__.py` | Python包兼容 |
| `action_schema.py` | 中枢与控制使用的动作协议 |
| `channel_policy.py` | 旧设备基线通道排除接口 |
| `quality.py` | 旧基线QC时间范围与mask语义 |
| `quality_continuity.py` | 旧基线质量连续性语义 |
| `gpu_placement.py` | 物理第二张A100限制 |

不含训练入口、网络结构、优化器、JEPA/fit80/GRU训练逻辑或模型权重。模块保留不代表新设备配置/QC已适配；后续移植时应把真正通用的逻辑迁入新的公共包，再移除这些兼容路径。不得恢复旧训练队列作为新设备方案。
