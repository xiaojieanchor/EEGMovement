# g.Nautilus离线研究准备 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不接设备／不搭实际链路条件下，完成可执行的研究准备核心、采集与评估方案、新版资料核查和保守的数据准入。

**Architecture:** 独立研究包只处理显式输入的数值／契约，不引用旧训练体系。数据合同、因果状态、固定特征模型、统计评估与离线决策分离；公开数据保持隔离。真实拟合和运行入口不由合成测试授予资格。

**Tech Stack:** Python 3、NumPy、SciPy、unittest；JSON、Markdown；已有PDF工具。

**Spec:** [design.md](design.md)

## Global Constraints

- 不改hub／unity／receiver／旧training兼容模块与device_transition.json，不连接设备／服务器或创建网络服务。
- 不执行厂商代码与安装器；下载来源可追溯、许可可见，无登录或购买绕过。
- 新实际EEG运行仍受第二A100 UUID约束；本轮数值测试只用合成数据，不启动真实训练／推理／诊断。
- 未知元数据、原生任务不符或来源交叉即拒绝准入；REST、拒识、未知和质量失败身份不合并。

## Review Focus

未来输入混入当前缓存；同一原始来源换名跨划分；CAR低秩／坏道改变CSP维度；margin冒充概率；拒识或预热被移出指标分母。每项均有明确数值或契约回归。

## Tasks

- [x] T1：编写合成契约与因果处理失败测试，运行确认失败；实现`gnautilus_lab/contracts.py`与`signal.py`，验证分包一致、未知参数拒绝和缺口预热。
- [x] T2：编写CSP／LDA与分组评估失败测试；实现`reference.py`与`assessment.py`。核心拟合函数仅供受治理的未来入口和显式合成单元测试，当前无真实训练CLI。
- [x] T3：编写状态与指标失败测试；实现`decision.py`，不输出实际命令。新增CLI只检查离线契约和协议身份，不对真实EEG评分。
- [x] T4：整合采集协议、推荐点位、故障矩阵、模型／MATLAB取舍；核查官方更新并实际尝试小文件下载，保存成功／失败收据。
- [x] T5：对候选公开库建立准入登记并隔离小样本。验证来源、许可、内容格式和原标签，不自动融合。
- [x] T6：独立审阅与相关回归，输出MD／简洁PDF、使用说明、验证证据和API提供后的具体接续步骤。

项目不是Git仓库，新增内容通过SHA和运行基线核对，不伪造提交／工作树。研究子任务各写独立目录，最终由主任务复核整合。

完成身份：T6使用作者单独证据与边界复核，不称新的独立代理审阅。三个研究任务因额度终止后的缺项已由主任务接续完成，未重启它们。实际硬件与服务仍按用户指令等待API。
