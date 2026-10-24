# g.Nautilus 数据全链路实施记录

本文件记录用户于2026-10-22批准的方案及实现边界。原厂负责配置和设备启停；程序仅参与共享会话。主动标签关联接收样本，不承诺硬件同步。无新模型训练或旧模型恢复。软件验证与实机验收分别记账。

## 模块分工与接口

- `gnautilus_acquisition/contract.py`：描述、样本块及TLS帧协议，独立于旧中枢七路处理。
- `gnautilus_acquisition/storage.py`：float32追加原始数据、逐块提交日志、恢复与关闭清单；两端共用。
- `gnautilus_acquisition/sdk.py`、`worker.py`：原厂CFFI参与者和独立工作进程。SDK仅访问采集电脑loopback服务。
- `gnautilus_acquisition/client.py`、`gui.py`：监督、落盘、主动TLS连接和中文界面。
- `hub/gnautilus.py`：TLS服务、配对和数据流状态；`hub/app.py`分流新设备状态与记录，`hub/acquisition.py`使用新记录器。
- `hub/quest.py`、`hub/vr.py`和Unity相关脚本：身份配对、NAT回包、动态通道显示及打标路径适配。
- 报告增量与模型索引放`docs/gnautilus/integration_20261022/`，旧证据保持原身份。

### 描述契约 gnautilus.stream.v1

JSON字段：`schema`、`stream_id`(32位hex UUID)、`epoch`(非负整数)、`source_kind`(`gtec_live`或`synthetic`)、`device_serial`、`sampling_rate`、`channels`、`configuration`、`configuration_sha256`、`layout_verified`、`reference`、`ground`、`wearing_id`、`role`。每列含`id/name/kind/unit/physical_index`；kind为`EEG/ACC/COUNTER/LINK/BATTERY/DIGITAL/VALIDATION`。EEG为uV，附加信号使用自己的单位。未知位置不得由候选蒙太奇自动确认为实际位置。

样本块：`stream_id/epoch/seq/sample_start/sample_count/read_monotonic_ns/utc_ns`加C连续little-endian float32矩阵。sample_start为本流实际接收行号，不填补设备缺样；硬件counter单列原值保留。配置变化创建新stream_id。来源、设备失效和断点独立于电极接触质量。

传输：4字节network-endian总长 + 4字节JSON头长 + UTF-8 JSON头 + 可选float32体，总帧不超过4MiB。TLS固定证书指纹，配对凭据经TLS验证；每连接有随机挑战，不能只凭IP。消息hello/descriptor/block/ack/status/close；block区分live/backfill，ack区分received/durable序号。只有新鲜live块进入中枢实时缓存；backfill仅存储。实时新鲜度默认250ms，接收双方单调时钟不直接相减。

### 记录与标签

流日志包含原始矩阵和提交索引、描述、事件、检查点、关闭清单；追加块在fsync后才获durable确认。恢复截去未提交尾部，已提交损坏必须报错。标签保留sender时间、hub接收时间、epoch、stream_id及sample_index_end；UNKNOWN不补REST。程序停止只停自身streaming，绝不停止原厂采集。

## 执行账本

- 2026-10-22：进入实施。工作区不是Git仓库；采用定向备份、文件SHA和验收清单，不初始化Git、不复制3GB厂商目录。
- Pre-flight：SDK输出动态列与采样率，contract负责验证；Hub新入口不得调用旧滤波/七路QC。NAT身份以签名为依据，地址只作当前回包端点。
- 原厂共享能力为实机前提；独占会话明确拒绝，不静默接管。SDK服务/硬件不存在不以模拟测试代替。

## 已实施范围与验收记录

已完成材料全量复查、86个新增唯一模型卡片、原报告修订、Python/C ABI逐项分析；所有10067个原始文件重新核对SHA且保持不变。programfile_gtec为9307文件、231物理模型／160唯一内容；三个来源合计244物理模型／170唯一内容。旧84个模型身份保留。

已完成独立CFFI参与者、SDK隔离进程、有界写盘队列、TLS主动连接、历史补传及持久化确认；只读audit核对逻辑块序与关闭清单。中枢独立数据入口使用动态通道、实际采样率及原始单位；接触质量未知，新模型未配置。主动标签、UNKNOWN、750ms按住租约和断流结束保持有效。

采集端采用PySide6工作台，最近5秒／8路分页原始波形、固定量程、冻结显示、峰值包络、减少动效、连接分层检查、中枢组件日志及导出。验证包含空闲与明确标识的合成波形夹具离屏渲染，以及冻结EXE的真实Qt依赖加载；不会在生产入口添加演示信号替代原始EEG。

Quest与UDP按签名身份处理，同一NAT后的多个身份不绑定同一IP；回包使用当前认证来源，映射改变停止并重新握手。Android与Windows VR构建分别冻结源码和哈希，无头显安装。

构建中曾出现错误打包宿主PDF工具链的ICU库导致QtCore加载失败；最终构建隔离PATH，仅使用Python／Qt及操作系统依赖，并以冻结EXE依赖、SDK子进程错误路径和Qt离屏窗口验证。首次失败未记为通过。

最终测试数量、构建SHA、ZIP校验和PDF检查以verification/delivery.json与各原始日志为准。原生界面完成离屏排版检查；本机浏览器使用被Work设置禁用，Hub网页只完成HTTP与JavaScript验证，不记为浏览器视觉验收。真实原厂共享、32路EEG、校园网络及头显操作全部待现场确认。未恢复旧模型、启动训练、操作服务器或部署到其他设备。

独立代码审查发现并以先失败后修复的回归补齐：中枢慢fsync后过期块仍进入实时、跨日服务重启错误新建同流目录、Hub隐藏已回传的采集端具体故障。已分别改为提交后重判并保留剩余时效、跨日期复用同一流、展示SDK／写盘／错误的最后回报；SDK调用开始至复制完成的观察区间也纳入预算。原厂内部积压年龄保持实机未知。
