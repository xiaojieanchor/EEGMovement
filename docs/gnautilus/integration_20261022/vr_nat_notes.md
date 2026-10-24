# VR 与 NAT 适配实施记录

## 身份与回包

Quest HTTP 仍使用独立设备ID、启动ID、严格签名和64项序号重放窗口；源IP只登记为 observed_address，不作为身份。最多4个配对设备，每个设备必须有不同密钥。删除或轮换配对会同时撤销UDP身份。

UDP v1信封、HMAC和接收端票据时效保持；中枢对最多4个已登记密钥作有界验证，认证后才读取中枢就绪状态，并向本次心跳的真实源端点回包。接收器ID/nonce绑定配对身份，一个配对身份仅可有一个新鲜接收器。

认证成功但NAT端口改变时，中枢撤销TEST并返回rebind，既不更新旧端点也不续last_seen。更新后的共享C#核心验证当前nonce和本地未过期票据后立即停止、换nonce并重新握手；旧nonce、票据和命令失效。重放的welcome仍不能重置序号。已退休nonce保留至配对撤销，最多4096条，达到上限明确要求重新配对，不静默删除安全历史。

## 校园地址与部署

默认仍接受私有LAN地址。若中枢为校园分配的公网IPv4，部署工具显式参数`--allow-campus-host`写入受控配对文件的explicit_host标记。只有该标记才允许目标公网单播IPv4；0/8、其他loopback、link-local、multicast和broadcast不接受。没有公开管理端点。

本轮没有安装、启动或重配任何Quest设备。旧APK不理解rebind事件，应安装本轮构建才能完成自动端点变化握手。校园网若隔离下级路由的连出，程序不能绕过隔离；需现场把三端接入同一路由或AP。

## 状态与主动打标

状态schema升至3，完整保留至256列的受控通道状态，含kind/unit、实际与标称采样率、EEG列数及contact_quality_known；不再截断至7路。头显状态页每4秒展示8列，保持完整通道可查；未知电极接触质量显示未测量。移除固定JEPA名称及缺失成绩被显示为0%的旧文案。

Android采集只走已配对的QuestNetwork；未配对时明确报错，不回落到头显自身127.0.0.1。电脑预览仍使用本机管理接口。保留既有主动事件、松开与提示任务逻辑，文案更新为原厂共享会话和独立采集端。

## 与根任务衔接

- `retired_action`使用第四个参数`getattr(hub,'new_device_mode',False)`，根任务需提供对应新设备例外。
- `/api/quest/reload`必须调用`QuestService.reload_enrollments(records)`，不要仅清空devices后register，否则撤销设备的UDP密钥可能残留。
- 根任务提供source.device、nominal_sfreq和quality.channels的kind/unit/contact_quality_known。
- `verification/vr_nat_verification.json`及日志记录16项NAT用例（含实际localhost UDP回包）和45项共享C#核心测试通过。本记录不等于真实校园网络、头显渲染或EEG实机验收。
