# g.Nautilus 离线研究准备核心

这是新设备独立研究组件，不导入旧`training/`，不连接设备、打开端口、启动服务或驱动VR。当前没有真实EEG训练／推理入口；`ReferencePipeline.fit`会在生产上下文拒绝执行。测试只对合成数组显式mock该检查，不存在环境变量跳过开关。后续必须结合实际API建立新训练治理与第二A100运行凭据，不能把这个数值核查包当上线模型。

可用内容：

- `contracts.py`：单位／采样／参考／哈希及角色检查，同源、同日、同佩戴跨集合拦截；公开数据保持原生任务。
- `signal.py`：因果有状态滤波及滑动功率，分包一致，缺口／epoch／无效样本重置与预热。它不决定电气QC资格。
- `reference.py`：正则化二类CSP→对数归一方差→训练内标准化→收缩LDA，输出未校准margin；NPZ无pickle导出。无多类或速度能力声明。
- `assessment.py`：逐组重拟合与嵌套选择、覆盖／含拒识成功率、泊松误启动上界、会话bootstrap与受条件限制的整块置换。
- `decision.py`：离线证据累计，明确概率语义、新样本、时效及状态重置。参数只是研究模拟参数，不改变现有阈值。

在项目根目录运行：

```powershell
python -m unittest discover -s tests -p test_gnautilus_lab.py -v
python -m gnautilus_lab capabilities
python -m gnautilus_lab check-registry docs/gnautilus/iteration_20261022/datasets/admission_registry.json
```

输入合同检查用`check-record <记录元数据.json>`。采样率、端口、参考与设备配置SHA尚未确定时，检查应失败，而不是猜值运行。原始厂商值只有确认单位和缩放后才转换到本研究内部uV合同，原始文件始终保留。

NumPy／SciPy使用现有项目依赖，无额外安装。合成测试验证数学、隔离和边界，不构成设备精度、神经来源、高置信可分性、实时延迟或真实VR验收。
