# EEGMovement VR Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成可构建的Unity空间界面、真实状态仪表盘与最终链路验收。

**Architecture:** 保持当前采集/GPU/签名控制核心，以独立Unity表现层和准备状态汇总模块扩展。Unity、准备接口、网页分别限定文件所有权；共享协议由spec固定后并行执行，集成构建与真实链路由主代理统一操作。

**Tech Stack:** Unity6000.3.26f1 / UGUI / 官方OpenXR与XR Interaction Toolkit；Python3.13、原生HTML/CSS/JS；现有C# Core。

**Spec:** `docs/superpowers/specs/2026-10-15-vr-workspace-design.md`

## Global Constraints

- LIVE不装入意图模型；180ms租约、签名和实际应用回执不能削弱。
- 安装与构建在G盘；hub/runtime、密钥、账号配置不能进入交付包或日志。
- 用户已授权自主推进，无需分阶段确认。当前目录不是Git仓库；以备份/哈希/日志代替提交，不虚构worktree。
- Unity运行健康字段和/api/preparation字段按spec；各任务只改分配文件，禁止重启主中枢或共同运行Editor。
- 两个A100原任务不停止；正式训练不启动。
- 用户最新约束：不操控桌面鼠标、键盘或窗口。软件操作和验收使用API、签名遥测、进程管理和CLI；样式的主观审阅由用户体验后反馈。此前的浏览器/截图步骤仅作为已做的历史检查，后续不再操作UI。

## Review Focus

- 旧运行程序/新源码混用：构建ID与源码哈希不匹配必须暴露。
- 网络断开后页面不得保留“在线/正在动”的历史假象。
- 用户重复点击/切页/复位不能开启TEST或自动恢复运动。
- Unity主线程卡顿、碰撞后回执必须报告真实位移，停止不等动画。
- 训练证据缺失、损坏、旧设备profile不能被汇总成formal ready。

## Task 1: Unity界面与运行健康

**Files:** `unity/EEGMovement/Assets/**`, `Packages/**`, `ProjectSettings/**`。不改hub、网页或训练。

**Interfaces:** 保持现有签名协议；可选运行健康字段按spec。提供`BuildSandbox.Windows()`、`BuildSandbox.Android()`、编辑器场景验证入口与构建ID记录；G盘输出路径由环境变量指定。

- [x] 先添加可执行的UI状态/运行健康/急停与复位测试，运行观察失败；复用C# Core测试入口或Unity编辑器验证，测试代码不嵌入产品类。
- [x] 实现UGUI/空间面板、清楚的导航/控件反馈/可读字体/减少动态效果/立即停止，保留控制核心。
- [x] 增加主线程运行健康与构建身份，旧C#协议继续可用；用测试覆盖字段与TEST状态不能自动开启。
- [x] 准备XR与Android构建入口；PC无头显时可运行。版本从官方registry锁定。
- [x] 主代理统一运行最终Unity编译、编辑器检查、实际渲染帧与构建（最终样式由用户体验审阅，不操控桌面截图）；任务代理不启动Editor。

## Task 2: 准备状态与真实执行接口

**Files:** `hub/preparation.py`, `hub/app.py`, `hub/vr.py`, `scripts/check_training_readiness.py`, `scripts/snapshot_execution_release.py`, `tests/test_preparation_status.py`, `tests/test_vr.py`。不改Unity或网页。

**Interfaces:** 生产`GET /api/preparation`，保留已有API与控制语义；消费spec规定的Unity运行健康字段与G盘记录、训练证据。

- [x] 先写缺失/损坏/过期/源码不匹配/无个人数据的汇总测试和运行健康字段验证测试，运行观察失败。
- [x] 实现有界缓存、脱离采集锁的状态汇总；只读证据，不执行外部命令、不读取/返回配对密钥。
- [x] 增加状态API，修正固定Unity安装false和静态执行来源文案，不削弱模型/个人数据门控。
- [x] 运行完整`python -m unittest discover -s tests -v`和训练单测，保留证据。

## Task 3: 中枢和联调页面

**Files:** `hub/web/**`，可新增纯前端测试文件。不得改Python或Unity。

**Interfaces:** 消费已有`/api/status`、`/api/vr/status`及spec的`/api/preparation`；POST行为保持。

- [x] 为新鲜/过期/缺失接收器、请求pending和TEST禁用条件写可执行行为检查，先观察旧实现失败。
- [x] 实现Apple参考的克制功能层、清楚状态层级、准备状态面板和真实执行卡片；支持320px、键盘、高对比和减少动态效果。
- [x] 点击等待中阻止重复提交，错误有恢复入口，停止随时可用；不要隐藏有效校验或因文件已有效而禁用复查。
- [x] 用JS语法/行为检查验证；主代理用真实浏览器检查宽/窄布局与关键按钮。

## Task 4: 集成、最终构建和全链路

**Files:** `scripts/`中的验收/构建/打包脚本、`outputs/`证据、README与交付文档；必要时对集成问题定点修复。

- [x] 改版前源文件备份到G盘。补齐Android模块，运行java/javac/adb/cmake/clang版本与签名检查。
- [x] 核对各任务接口和变更；重跑全部本机测试和共用C#测试，修复重要失败。
- [x] Unity实际编译/场景生成/空间绑定与安全逻辑检查/Windows构建；具备条件时Android构建；记录构建ID、源码与产物哈希。
- [x] 平稳重启本机中枢以加载新后端，凭据仅通过隐藏输入/当前进程内存传递；确认采集/GPU恢复。
- [x] 用最终程序替代临时C#接收器执行链路验收，运行方向、停止、急停、失联与故障注入；不得把网页轨迹当Unity渲染证据。结束保持LIVE静止。
- [x] 重跑训练readiness、权重/数据/设备门控检查，仪表盘与报告一致。
- [x] 独立代码审阅指出的问题已修复并重跑覆盖检查；追加采集与非阻塞校验由主代理审查和真实集成测试，审阅范围见outputs/代码审阅与修复_20261015.md。
- [x] 封存当前全部Windows/Android/源码、哈希与资源索引；私有runtime与大数据排除，最终归档后再次验证信号通路。

## 执行记录

- 2026-10-15：用户批准推荐方案与自主实施。改版前Python基线36项、训练基线29项通过；旧UnityWindows构建成功，作为基线而非新版验收。
- Ruling: 当前无Git仓库，直接在用户指定工作区按文件所有权修改并保留G盘备份；不建立无关仓库。共享schema固定后并行实现，最终运行/构建串行协调，避免干扰运行服务。

- 2026-10-15 15:25：101项Hub、29项训练CPU、21项前端、34项共用Core、32项工作区、26项构建摘要行为检查已通过；后端已实际重启，官方Socket/A100返回恢复。Android实际APK编译0错误，但首次强完整性检查发现两处官方自动设置改写，已保留失败证据并在稳定设置基础上重试。最终Windows构建及全链路仍待本轮验证，不以此前C#结果代替。

## 用户补充：VR内采集辅助程序

- [x] 设计内置脑电采集页、中心注视光标、随机平衡REST/四方向任务、自动软件事件与中枢原始EEG同文件保存。见docs/vr-acquisition-design.md。
- [x] 实现中枢独占会话、顺序ACK/幂等重试、源/epoch/QC/时钟误差/头姿记录、超时保存与正式训练门控；11项行为单测通过。
- [x] Unity内置开始/暂停/继续/停止、任务阶段固定光标、实际end-of-frame事件与保存确认；加入显式CLI真实组件集成检查入口。
- [x] 最终Unity构建后实际跑完区组，核对显示帧、20个事件、五标签平衡、EEG原始样本、终止/超时与中枢状态；重打包并从包内程序重跑信号通路。

Android无采集功能的上一构建已成功且源快照稳定，build eeg-4f3c9963abaf13b8d7786f40；用户新增采集辅助功能后正在重建，不将其当最终交付。

- 2026-10-15 16:14：最终Windows eeg-5090e28f200a0d4aada1b5a3（以build-status.json完整ID为准）与Android eeg-749888...均成功且源码快照稳定。VR内置采集实际完成20事件/8905样本，包内复测20事件/8535样本；包内方向/停止/SSH重连持续观测通过。新增后台证据重验修复，实时API最大30.231ms。正在将最终后端与验收工具封入新版源码包，随后只复跑信号通路，不再改源码。

- 最终封存：334项软件检查通过，Windows/Android/全源码档案、构建全输入覆盖和完整Windows文件树逐项审计通过；包后信号与60秒故障观测、实际采集区组复测通过。当前正式训练、Quest硬件/独立联网与真实佩戴仍按报告独立阻断。
