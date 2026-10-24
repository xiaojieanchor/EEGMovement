# Quest 3S 独立运行与局域网预览

应用包名 `org.eegmovement.workspace`，原生ARM64/OpenXR。USB仅用于ADB安装和写入设备专用配对信息；运行时不使用adb reverse/forward。电脑中枢管理页面仍只监听127.0.0.1:8765，独立设备API为TCP8766，执行签名UDP为19771。必须允许Quest到电脑的实际Wi-Fi地址互通；不同子网/访客隔离不能仅凭“都连上Wi-Fi”视为可用。

## 安装与重配

启动电脑中枢后运行：

```powershell
C:\Python313\python.exe scripts/deploy_quest.py --serial 设备ADB序号 --hub-host 本机WiFiIPv4
```

脚本验证APK与成功构建摘要，安装更新，不卸载或清除数据；随机设备密钥只保存在hub/runtime与头显应用专用目录，不写入APK、研究资料或公开日志。网络地址变化后重新执行即可。Quest系统要求控制器时，由使用者佩戴头显、唤醒控制器并确认系统提示；安装成功不能替代运行验证。

当前脚本默认使用integration_20261022/verification/android-build.json对应的新APK，核对APK与源码摘要；本轮未执行头显安装。旧记录仅在新记录不存在时兼容使用。

成功构建后可运行 `C:\Python313\python.exe scripts/archive_android_build.py` 冻结全部构建输入，并重新核对源码、APK及ZIP内每个输入的SHA-256。快照保存在G盘SourceSnapshots；记录写入outputs/android_build_validation.json。运行和佩戴验收单独记录，不由构建成功自动推断。

HTTP请求和返回均经HMAC签名，身份绑定设备密钥；启动nonce与序列窗口防重放，UDP使用独立派生密钥。地址只作认证后观察到的回包端点，同一NAT后的多个设备可分别配对；端点改变时先STOP并重新握手，旧nonce不延长执行许可。配对返回只包含端点和公开session，不发送执行密钥。LAN接口只允许配对刷新、采集事件和应用画面上传，不开放服务器密码、管理配置、关闭服务或TEST指令入口。HTTP最多16并发，正文/帧/频率有上限；指令继续保留接收端单调ticket、180ms租约、序号和STOP门控。LIVE意图模型仍未启用。

## 画面和状态

中枢首页“Quest应用实时预览”显示头显应用的中心视角。默认960×540、JPEG75、约6帧/秒目标；仅保留最新帧在内存，不默认录像。它不包含系统菜单、双眼畸变画面、透视摄像头或音频。头显本体使用自己的双眼渲染分辨率，不能拿预览图尺寸代表头显分辨率。读取/上传任何时刻最多一帧，慢网不排队旧画面；2秒无新帧即隐藏过期预览。

只读接口：`/api/quest/status`、`/api/quest/frame.jpg`、`/api/vr/status`。头显休眠或退出前台时，预览和回执应分别过期，不能保留旧状态冒称在线。实际连续验证：

```powershell
C:\Python313\python.exe scripts/validate_quest_runtime.py --seconds 60 --wait 240
```

使用者需保持头显应用前台。检查实际XR渲染、当前构建身份、LAN回执推进、真实JPEG及失鲜率；不会发合成运动或自动点击。通过不等于用户已接受交互或真实脑电意图识别有效。

## 头显交互

OpenXR pointerPosition/pointerRotation（aimPose）驱动UI射线，握持gripPose不作为瞄准方向。姿态及扳机在XRI采样前更新；XR模式关闭桌面GraphicRaycaster、鼠标和手柄导航的重复UI输入。射线与面板平面的连续交点驱动光标，按钮命中只改变可点击反馈，不吸附/覆盖坐标。光标排序高于面板，在空白区域也可见；落点、UI采样和点击使用同一条射线。

头显采用1040×760逻辑布局、1.55米面板距离、较大字体与触控目标；动态字形密度2、XR纹理倍率1.1、4倍MSAA，并关闭场景阴影降低负载。悬停/按压改变颜色及边框，不缩放或移动点击矩形；左右手分别记录pointer状态。确认动作由按下/同一按钮释放完成，急停在按下即执行。

实际手感与清晰度需由佩戴者复测。运行日志使用EEG_XR_CLARITY、EEG_XR_POINTER、EEG_XR_CURSOR、EEG_UI_CLICK标记；坐标连续和按钮事件应分别检查，不以几个按钮间的高亮变化代替光标连续性证据。

窗口操作不依赖先找到面板：左手X显示/隐藏，左手Y将当前页面召回眼前；隐藏后重新显示也会居中。标题右侧“隐藏×”提供同等入口。用任一控制器的扳机按住标题栏可拖动放置，松开固定；拖动时该手摇杆前后调整距离，限制在可读距离内。面板保留当前页面，不持续跟随头部。MENU仍为急停。

右手A开关设置窗口，右手B按住立即显示只读实时监控、松开隐藏，长按右手Meta通过系统原点更新重置场景视角；左手快捷键仍可用。监控与角色说明见character-and-monitor.md。默认明确请求Device追踪原点，并在佩戴、追踪及焦点稳定后一次性建立1.65米虚拟坐姿眼高。设置页“校准视高”可重新建立当前姿势的基准；只平移视觉原点，不改写相机追踪姿态或角色执行坐标，不每帧锁定高度，保留真实6DoF移动。这是应用内的虚拟视高，不替代Quest安全边界/地面校准。

Meta系统菜单造成焦点或设备活动状态变化时，XR模式按已初始化的loader/session维持，桌面相机禁止参与双眼渲染；不切回桌面相机、不旋转视觉原点。Android输入系统允许继续接收追踪，控制器UI则单独按焦点阻断，并在焦点变化时取消拖动、等待扳机释放。失焦仍急停、中止采集；返回不自动复位。`EEG_XR_MODE`、`EEG_XR_FOCUS`、`EEG_XR_POSE`、`EEG_XR_HEIGHT`用于核对实际切换与眼高，系统菜单下的佩戴效果需单独验收。

焦点同时检查Application与XRDisplaySubsystem.displayFocusChanged，避免只监听Android窗口焦点遗漏XR系统浮窗。运行 `C:\Python313\python.exe scripts/inspect_quest_tracking.py` 可只读提取当前应用进程日志和接收端构建身份，生成quest_tracking_validation.json；报告明确区分相机结构检查、实际显示焦点往返与用户佩戴接受，不能互相替代。

实现依据：[Unity XR Origin高度语义](https://docs.unity.cn/Packages/com.unity.xr.core-utils%402.2/manual/xr-origin-reference.html)、[输入焦点设置](https://docs.unity3d.com/Packages/com.unity.inputsystem@1.14/manual/Settings.html#background-behavior)、[XR显示焦点事件](https://docs.unity3d.com/cn/2019.4/ScriptReference/XR.XRDisplaySubsystem-displayFocusChanged.html)。实际工程API同时按已安装的XR Core Utils、Input System和XR Management源码核对。

隐藏面板会中止并保存正在进行的采集，撤销TEST许可；重新显示不自动恢复采集或运动。隐藏、召回和标题拖动分别记录EEG_PANEL hidden/summoned、EEG_PANEL_DRAG begin/placed，必须结合真实手柄操作验收。

## 范围

本轮增加独立Quest传输和应用预览，未完成自然意图模型、速度头、物理发光时序或真实佩戴抗伪迹验收。实际g.Nautilus数据失鲜时禁止采集入口并显示原因，角色保持静止；不会用网络联通冒充脑电质量通过。
