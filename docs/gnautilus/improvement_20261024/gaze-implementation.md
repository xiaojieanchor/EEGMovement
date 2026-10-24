# 眼动优先实施记录

依据：用户已批准训练与模型库实施方案，并于2026-10-24明确优先眼动方向，不要求四肢想象；正常VR手柄操作应被允许。

1. 保留已完成实验，取消未开始的四肢任务；不恢复旧队列。
2. 默认采集模式改为gaze_only；提示分别表达方向、回中、无控制、干扰，不把无肢体任务写成无导航意图。
3. 核查公开EOG数据的注视目标、回中、眨眼、头动、单位及分组。原始数据、旧派生和成绩不覆盖。
4. 在服务器建立新的源绑定派生，按完整参与者分组，运行正式治理入口后训练、评估、自动登记；不自动加载或允许动作。
5. 保持中枢原页面结构，补充眼动路线说明与结果文档，回归接口和采集语义。

验收：方向提示不含四肢想象；回中不改成反方向；眨眼任务不标为REST；训练/开发与覆盖率并列；公开独立EOG不得声称可直接用于32路头皮EEG或已通过VR实测。

## 执行账本

- 已读取当前AGENTS、新设备训练约束和既有源码。当前目录没有Git；沿用现有工程原地修改，服务器每版源码采用不可变发布目录，变更文件另行备份。不创建或恢复旧工程。
- 设计裁决：用户已明确训练路线和授权，无需重复设计审批。保持已有接口兼容，新增眼动语义使用新身份。
- 已取消45个排队的四肢任务；1个已运行任务保留其正常完成与产物提交，2个眼动研究任务继续。收据：gaze-priority-cancellations.json。
- 数据风险：Malta数据1的眼球位移标签不能当作绝对注视方向；返回中央必须单独研究。现有独立EOG幅值单位未确立，不能冒用µV质量门。

- Task 1 complete: 45 queued limb jobs cancelled; completed artifacts retained. 20 gaze jobs completed and registered, 20 export and current-adapter replay checks have zero probability difference. No model selected, inference/actions stopped.
- Task 2 complete: gaze-only defaults and guided/manual metadata; actual desktop controller/DOM and Unity JsonUtility/UGUI consume instructions and allowed labels. Old unguided gaze clients refused before opening a record.
- Task 3 complete: source QC for 6/10/8 participant records; 13,187 source-bound target windows; original EOG units and coordinate limits remain explicit.
- Final review: fresh reviewer found P1 client guidance not consumed and P2 study namespace not bound to preparer. Both fixed with RED-to-GREEN client tests, real Unity component test and namespace test. No deferred review findings.
- Network follow-up: observed own HTTP accept-loop stall with full backlog, no active jobs/selected model. Controlled own-service restart, SSH error state update and 30s progress watchdog added. Original internal wait cause remains uncertain; diagnostics preserved. 24 concurrent API checks passed after recovery.
- Verification: 77 Python tests, four JavaScript suites, 47 C# core tests; Unity JsonUtility/UGUI validation passed. Quest 2.1.4 built and signed with original certificate, immutable APK then signed manifest published; TLS/range/cloud SHA verified; Windows manifest unchanged. No USB Quest device available, so no installation/hardware pass claimed.
- PDF: Songti, black/white unshaded three-line tables; final pages rendered and reviewed. No Git repository, no branch/merge operation.
