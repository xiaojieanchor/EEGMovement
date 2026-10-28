# 人工眼动标注与混合域训练 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline. The user already authorized implementation and routine experiment decisions. Do not request repeated approval.

**Goal:** 将用户完成的人工眼动标注锁成可复核的训练数据，并训练保留电极身份的离线眼动候选。

**Architecture:** 人工范围为唯一私人方向真值。原始试次及三集不变；同方向的首尾事件在各自角色内共享类别，尾部截断保留。公共域与私人域分开空间适配层、标准化和分类头，只共享兼容的时间卷积／GRU；不把 EOG 电极改名成头皮 EEG。

**Tech Stack:** stdlib provenance compiler; existing PyTorch Branch, A100 training authority, immutable NPZ/JSON receipts.

**Spec:** 用户2026-10-25本轮消息及本文件的 Global Constraints。

## Global Constraints

- 私人类别为 NO_EYE、UP、DOWN、LEFT、RIGHT；NO_EYE 只指眼动静止，不等同导航 REST。
- 只有 marked 区间为方向真值；只有 onset=absent 的前2秒可提供无眼动基线。offset=absent 不授权把头部正事件改成静止；uncertain/pending/中间保持段不补标签。
- 严格绑定完整试次、来源流、绝对半开样本范围和原始 split。测试不用于训练、归一化、公共教师选择或模型选择。
- 32路物理顺序不变，CH16=Cz、CH19=C5；公共 EOG 原生通道和原生任务不变，单位未对齐不得拼原始幅值。
- 所有真实 EEG 处理／训练／推理在指定 A100；本地只编译元数据。预算最多1800秒，种子17/29/41，最多100轮，各轮一个私人全批次+两个公共原生域小批次；保存真实梯度、域计数、检查点和选择冻结回执。
- 不自动改线上模型、阈值、推理或动作。旧测试已查看，只报告同次佩戴内部研究。

## Review Focus

- 仅结尾 absent 的方向段：不能把首段阳性变成静止。
- 尾部记录截断：不补零、镜像、伪造完整波形，不丢掉已有范围。
- 跨角色同方向：可以共用类名，不可移入训练池。
- 开始上下文不足：用真实变长历史，不因固定预热丢人工事件。
- 公共原任务高分：按任务分别说明指标，不能直接称私人方向效果。

## Tasks

- [x] 1. `gnautilus_ml/ocular_event_data.py` + `tests/test_ocular_event_data.py`: RED→GREEN，编译标注、基线、截断和角色清单；原人工修订只读。
- [x] 2. `gnautilus_ml/ocular_event_training.py` + 治理分派及回归：锁定输入/代码/公共合同，真实变长因果支持、域分离模型、混合损失、选择冻结和测试隔离。
- [x] 3. 固定A100隔离源码，运行治理检查；保存版本化标注数据、三种子迁移/随机对照、选定候选及实际评估。不改注册表。
- [x] 4. 核验源哈希、全部事件支持、真实优化与角色分离；交付数据与结果说明至本聊天 outputs。

## Ledger

- Ruling: 延续当前用户指定的正在运行项目，新增文件和最小治理分派，不切换当前 dirty main；远端使用独立不可变源码目录，不影响当前服务。
- Ruling: “同类放一起”指按实际眼动方向共享训练类别，不授权改变既有完整试次留出角色。
- Ruling: 没有客观眼位／眼动仪，手工框选是当前监督来源；不把公开 gaze/target 标签改为私人事件标签。
- User steering: 只在眼动辅助头筛选通道。训练人工区间相对同试次邻近背景的稳健对比，生成前4、前8、各方向前2的并集、额部8路；全32路只作对照且不参与最终选择。邻近背景只作对比参照，不新增为无眼动真值。3种子×2训练方式×5通道方案，共30候选；仍以验证集选定、冻结后只评估一个测试候选。
- Task 1 complete: metadata compiler RED→GREEN，5 tests/5 subtests。
- Task 2 in progress: 15 synthetic tests/5 subtests pass；已验证未选通道影响严格为零、尾部不足4点池化保留、变长padding不进入历史。
- Review limitation: 已按原执行技能尝试独立只读审查，但代理因账户额度限制未运行；执行者继续代码和数值核验，不将该尝试称为通过审查。
- Task 2 complete: `pytest tests/test_ocular_channel_selection.py tests/test_ocular_event_data.py tests/test_ocular_event_training.py tests/test_ocular_annotations.py tests/test_contextual_readout.py tests/test_navigation_jepa_training.py tests/test_joint_navigation.py` → 62 passed / 5 subtests。A100新模块15项通过；实际治理preflight通过。
- Task 3 complete: release `9b29da7c6e36ae4c3336e7c1d9a25d6953e64b635455be874f50bdc8a787f4fa`；协议 `d6f0a0ebc3d1cbe0721ab5bb90baa13cceeecc02a134d74de67083a2bf028995`。30候选、3000真实更新、262.13秒；PID930507实际GPU UUID核验通过。旧 `bbb1670f...` 仅准备未启动，已因新通道需求退出；预算保守计入其1800秒上限，不伪报实际训练。
- Result: validation选中contrast_top8-manual_scratch-17第47轮，事件宏召回70%；冻结后测试20%（2/9普通准确率22.22%）；连续源网格标注帧宏召回19.54%。NO_EYE三集召回均0；测试不通过，不上线。5路方向并集直接训练三个种子验证均为60%，标为新增数据后的重点对照。
- Task 4 complete: 141个人来源、127公共权重、全部26监督段末点、跨角色与未来支持零交集、先冻结再测试均通过。实际人工标注哈希未改，UI/模型选择/动作状态不变；8张图和离线工件交付。数据目录索引刷新：864份派生元数据，索引不授予训练角色。
