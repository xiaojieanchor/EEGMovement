# EEGMovement 空间界面与中枢状态改版

用户已认可 `outputs/VR界面审阅与改版建议_20261015.md` 的推荐方案，并于本会话明确授权后续自主设计与实施，无需分阶段确认。本文固定实现边界和共享接口。

## 目标

Unity 中的人物反馈是主体，常用控制清楚、稳定、可立即停止；桌面中枢统一显示真实采集、处理、GPU、模型、Unity 与最终执行状态。最终程序构建后重跑链路，软件通过与真实佩戴/Quest验收分别记录。

## Unity 体验

替换现有 IMGUI 为可缩放的 UGUI 界面；准备可用的世界空间 Canvas 与 XR 射线输入路径，桌面鼠标/键盘复用同一操作模型。使用内置渲染管线和官方 Unity XR 包，具体版本经 Package Manager 解析后锁定。头显不可用时仍能正常运行桌面模式。

界面入口为“体验 / 链路状态 / 设置”。默认静止、TEST未允许；主操作显示明确原因而非空白禁用。用户显式允许后，测试指令只能来自中枢的有期限 TEST，按钮/连接/复位不能绕过控制核心或装入意图模型。急停与Esc立即锁止；复位后保持静止，重连后不自动恢复动作。重新居中只处理视觉视角，不能偷偷重置控制坐标或改变真实运动回执。

功能层用少量圆角/半透明材质；数值、诊断和文字用稳定对比背景。支持减少动态效果、减少透明效果及可读字体；不分发 Apple 字体或图标。按钮按压约100ms，面板180–240ms，急停/失联状态立即生效。空间尺寸需头显实测，不将屏幕px当作角尺寸。背景装饰不闪烁，不生成未经时序验证的SSVEP刺激。

保留180ms接收端租约、签名/随机数/会话/去重/乱序检查、两次独立决策确认、速度限制、实际Transform应用后的回执。控制协议v1保持兼容，可增加可选运行健康字段：

| 字段 | 类型/约束 |
|---|---|
| app_version, build_id, ui_state | 字符串，最多120字符 |
| frame_ms, frame_p95_ms | 有限非负数，不大于10000 |
| rendered_frames | 非负整数 |
| focused, paused, xr_active | bool |

没有运行健康字段的旧接收器仍可连接；不能因此宣称已验收Unity渲染。运行健康值在Unity主线程采集；网络线程不读取UnityEngine对象。构建ID来自当前源码与依赖的摘要，并写入构建记录；最终验收必须核对它。

## 中枢接口

`GET /api/preparation` 返回：

```json
{
  "version": 1,
  "checked_at": "ISO8601",
  "status": "blocked",
  "software_ready": false,
  "formal_training_ready": false,
  "live_control_enabled": false,
  "stages": [{"id":"unity_build","label":"Unity构建","status":"ok","summary":"Windows已构建","source":"artifact","checked_at":"ISO8601","age_ms":0}],
  "blockers": [{"id":"device_mapping","label":"设备线路待核验","reason":"...","next_action":"..."}],
  "artifacts": {},
  "training": {}
}
```

stage状态固定为 `ok / warn / blocked / offline / unknown / working`。id包含 `acquisition, processing, ssh, gpu, model, unity_install, unity_build, unity_runtime, execution, training_software, training_data, montage`；允许额外阶段。字段来自当前Hub快照、G盘工具/构建记录、训练证据和最终验收文件。每项标明来源与时间；丢失、损坏、过期或哈希不匹配不显示为已通过。文件读取使用有界缓存，不能在采集锁内进行慢磁盘操作。接口不启动训练、安装或外部命令，不暴露密码、配对密钥或完整运行配置。

保留 `/api/status`、`/api/vr/status` 及现有POST语义。运动卡片按新鲜接收端的 `actual_speed/actual_yaw_rate/state/mode`显示；不能把固定零值当作实际执行。无新鲜回执显示未知/过期。优先显示Unity，其次C#联调；明确标注来源。采集、GPU、模型、输出解锁分别显示。

## 页面与点击

中枢增加完整准备/系统状态视图；联调页保留轨迹与动作按钮，强化TEST说明、当前接收器和最终执行状态。玻璃仅用于导航与悬浮控制层。按钮有可见的hover/focus/pressed/disabled/pending；阻止重复请求；停止始终可用。支持键盘、320px起的紧凑布局、减少动态效果和高对比。普通错误在相关控件处反馈，页面离开停止TEST。

## 训练准备

`scripts/check_training_readiness.py` 的Unity安装状态改为真实检测；不改变个人数据和设备证据门控。软件测试、服务器数值fixture、权重哈希、数据分组、训练/恢复/校准/测试入口分别显示证据时间。真实设备线路/参考/地未核验、未佩戴或无个人标签时，正式训练保持blocked。测试fixture不能作为意图效果证据。

## 交付与验收

程序、工具链和大文件在G盘。源码保留当前工作区，改版前备份到G盘；工作区不是Git仓库，不创建无关仓库或虚构提交。打包时排除hub/runtime、密码、配对密钥、用户账户配置、Library、Temp和大型权重/数据；提供哈希、资源索引和启动说明。

先通过本机Python和C#核心回归、Unity编译/场景/Windows构建，能完成的Android构建另外记录。再从最终Windows构建验证官方Socket→本机→A100→签名命令→Unity实际位移/旋转→应用回执→页面。覆盖静止、前后/转向、测试到期、停止、急停/复位、坏签名、乱序/重复/延迟、网络故障、帧停顿、重连保持静止、碰撞与状态过期。记录构建哈希与ID、样本数、延迟p50/p95/max、停止耗时与恢复结果。

没有头显或佩戴数据时，明确保留Quest显示、佩戴伪迹、意图准确率与意图到视觉<500ms为未验证；不以模拟、Windows构建或数据链路延迟代替。任何重要失败必须修复后再重跑相关最终检查。
