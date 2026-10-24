# Unity VR 工作区

工程根目录 `C:/Users/19410/Desktop/EEGMovement/unity/EEGMovement`，Editor为G盘Unity 6000.3.26f1，使用UGUI、Input System、官方OpenXR与XR Interaction Toolkit。中文字体Noto Sans CJK随附OFL许可，无付费资源。

## 运行和状态

先启动官方NeuraDock Socket和本机中枢，再运行根目录 `启动Unity体验.cmd`。启动器校验当前源码与完整Windows程序，使用19772端口，与临时C#联调程序默认19770端口分开。配对配置由本机中枢生成，只在私有runtime保存，不放进程序包。

默认TEST关闭，LIVE意图输出保持STOP。工作区分为体验、链路与设置，显示真实应用回执、构建身份、运行耗时及停止原因；急停一直可用。减少动态效果、减少透明、较大文字为本地偏好，不保存TEST权限。重新连接、离开链路页、急停与复位均撤销TEST。

主中枢 http://127.0.0.1:8765/ 与联调页 /lab 接入真实状态；/api/preparation单独核对完整构建、最终故障验收、运行新鲜度和训练条件。旧构建、失鲜或未知回执不能显示为当前停止或在线。

## 结构

- Core：签名协议、会话/nonce/ticket/序号校验、180ms接收端租约、独立决策确认、速度平滑、应用回执与纯C#安全状态。
- Runtime：CharacterController真实位移、应用后测量与回执、UGUI工作区、独立XR相机/控制器射线、暂停与急停。
- Editor：场景生成与官方OpenXR预检、平台构建、源码与完整产物哈希。
- Validation~：独立行为检查；目录不进入Unity导入。临时下载包和编译二进制不进入源码交付包。

20m场地包含可碰撞边界。FixedUpdate先消费签名指令和租约、执行CharacterController.Move，再以实际Transform变化测量速度并ConfirmFrame/Pulse；不能用目标速度冒称发生了运动。渲染帧计数来自相机OnPostRender，与命令ACK分别记录。

XR Origin在未激活时先绑定独立headset Camera、floor offset和官方TrackedPoseDriver，再激活；HMD姿态由UpdateAndBeforeRender更新。PC无头显时使用独立桌面相机。镜头/空间面板重定位不修改运动核心坐标。

## 构建和无桌面操控验收

G盘脚本：`G:/Unity/Build-EEGMovement-Android.ps1`、`G:/Unity/Build-EEGMovement.ps1`。同一工程只运行一个Editor。若先构建Android导致XR/平台设置变化，应最后构建Windows，使当前工作区与最终PC程序绑定。

每次构建重新生成场景，检查字体、组件与安全默认状态，运行官方OpenXR validation。build_id覆盖Assets、Packages、ProjectSettings的实际文件（排除生成identity及~目录）；构建前后摘要不同会明确失败，不能事后重写已嵌入程序的身份。artifact_hashes覆盖完整播放器，不能只校验固定Unity launcher EXE。

CLI参数：`--eeg-config <私有配对文件>`、`--eeg-port <1024..65535>`、显式 `--eeg-allow-test`。后者只许可合成TEST，不直接产生运动；仍需中枢新鲜状态、正确签名与有限租约。正常启动没有自动测试或控制端点。

显式 `--eeg-validate-safety <输出JSON>` 才创建一次性实际controller检查，验证急停、复位、nonce变化布尔值、TEST撤销、实际零回执和XR绑定，写有限报告后退出。其余API/故障验收命令见 `../docs/api-validation.md`。

## 证据与边界

当前结果以 `G:/Unity/build-status.json`、`android-build-status.json`、`outputs/unity_runtime_validation.json`、`outputs/execution_release_manifest.json` 为准，缺失/旧身份不得解释为通过。软件构建、运行、真实链路、Quest实机与意图准确率是不同证据。

当前签名UDP只绑定loopback，适用于PC程序（后续可经PC VR连接显示到头显）。Android APK构建不代表独立Quest已连接PC中枢；独立头显LAN配对与传输、控制器/显示、真实刺激时序仍待实现或实机验证。没有已校准的运动意图/速度意图模型，也没有真实佩戴伪迹鲁棒性结论。
