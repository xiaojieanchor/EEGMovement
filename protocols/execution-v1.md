# 执行链路协议 v1

本版是本机准备阶段：Python中枢UDP127.0.0.1:19771；接收器默认127.0.0.1:19770。角色为`csharp_harness`或`unity`，当前验证的是前者和共用Core。中枢HTTP仅127.0.0.1:8765，来源检查和自定义请求头保护保留。

## 消息与认证

UDP外层是`payload`与`mac`，payload为UTF-8 JSON的Base64，mac是对解码后原始字节的HMAC-SHA256十六进制签名。这样不依赖两端JSON的属性顺序或浮点文本格式。最大数据报4096字节，解码payload最多3000字节；坏签名、非有限数、未知版本和非法字段被拒绝。

随机32字节配对密钥、随机hub session每次中枢启动重新生成，保存到私有运行目录`hub/runtime/vr_pairing.json`。这个运行文件不经过HTTP返回，不写入会话与验证日志。接收器每秒检查配置变化，变化时停止、清空待处理消息并重新配对。该认证防止偶发数据、旧程序和未持有密钥的消息驱动角色，不意味着能抵御同账户可读取运行文件的恶意程序。

接收器发送heartbeat，包含：version、kind、receiver_id、nonce、session、ticket、role、allow_test、telemetry。中枢先回应welcome，再发送command。每个接收器最多保留128条待回执记录，最多4个接收器；收件队列最多8条。中枢陌生来源合计限流200报/s，已配对端点各自独立80报/s，接收器限流160报/s。超载会丢弃数据并让租约到期停止，不能无限积压旧指令。

## 不依赖跨机器时钟的有效期

ticket由接收器创建，绑定它自己的单调时钟。中枢只能回显ticket，不能提供新的接收端起始时间。接收器用 `ticket原始签发时间 + ttl_ms` 作为截止时刻，不能在迟到消息到达时重新获得180ms。

ttl限制50–180ms，并要求接收时至少剩25ms。未知ticket、错误nonce/receiver/session、重复或倒退seq、超过10000的异常序号跳跃、倒退epoch均拒绝。重复welcome不重置现有seq历史。

中枢对执行使用比界面更严格的时间门控：采集最后一包年龄<100ms，服务器结果年龄<150ms，结果对应的原始窗口尾包年龄<250ms。监控界面的连接状态不是控制许可。当前无已校准意图模型，LIVE的model_ready始终为false。

## 指令与控制

command字段包括session/nonce/receiver_id/ticket、seq、decision_id、epoch、ttl_ms、mode、active、input_valid、model_ready、direction、p_active、confidence、speed、yaw_rate、reason。

direction：0静止、1前进、2后退、3左转、4右转。进入/切换方向要求两次独立decision_id、接收端ticket间隔≥30ms，p_active≥0.8且方向confidence≥0.7。一次模型结果即使被多个网络心跳重发，也不会成为两次进入确认。活跃期间仍有较低保持阈值；低置信会把目标速度归零。

测试默认线速度0.6m/s、转速40°/s；硬限制1.2m/s与60°/s。进入运动的加速度0.9m/s²，正常静止减速度4m/s²。质量无效、模型未就绪、帧间隔>100ms、本地急停和租约超时直接制动。源/模式改变会清除确认状态。紧急制动优先于平滑；普通速度变化仍连续。

TEST模式要求接收器显式允许，且是独立合成输入；动作租约最长10秒，网页按钮默认2秒。当前TEST还依赖真实采集与GPU诊断的新鲜度。TEST的input_valid表示测试夹具有效，不代表当前EEG通过质检；LIVE不会因此被解锁。

## 回执和测量

received_seq表示接纳到控制核心；applied_seq必须在应用Transform/物理位移之后更新。telemetry带实际位置、朝向、实际速度、目标速度、停止原因、拒绝/坏包/队列丢弃和看门狗计数。

接收器往返耗时用它自己的ticket时钟测量。中枢记录“命令发出→执行回执收到”，其中包含等待下一次heartbeat的时间，是保守上界。LIVE诊断还记录“本机窗口尾包收到→STOP执行回执”，包含GPU诊断、调度、应用和回执；不是意图识别到VR显示的延迟。

接收器遥测超过300ms不再作为当前状态显示，不能因为发送了STOP就声称角色已经停止。没有回执时必须明确显示状态未知/过期。
