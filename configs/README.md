# 当前配置范围

`device_transition.json`声明g.Nautilus移植准备阶段与旧体系停用状态。

`execution_link.json`保留VR控制连接；`joint_control_policy.json`保留动作/控制协议。`device_channels.json`仅为旧设备兼容外壳保留，CH1/CH6损坏与七槽规则不适用于g.Nautilus。

旧训练、实验、划分、探针、资源计划和预算配置均移入 [历史configs](G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/retired/configs/)，不再作为活动训练方案。

`gnautilus_layout32_hybrid_candidate.example.json`已按用户16／19路互换更新：设备EEG16=Cz、EEG19=C5；JSON使用SDK零基键15／18，其余键为0..31。完整布局与REF/GND仍未验收，不能直接把verified当成已确认。
