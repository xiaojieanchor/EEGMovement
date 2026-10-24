# 双击Setup仍打开旧版的根因与修复

2026-10-23。Windows修复版本1.1.4，Quest保持2.1.2。

## 根因

1.1.3的安装包内确实是1.1.3工作台，但安装器和日常启动器共用`gnautilus_launcher.py`。旧入口条件为`args.install or not current.exists()`；用户直接双击没有`--install`参数，一旦存在旧`current.json`，就执行日常启动分支，根本没有解压或激活新版本。

本机实查`%LOCALAPPDATA%/EEGMovement/gNautilus-app/current.json`仍为`version=1.1.0`、`directory=versions/1.1.0-14f5eca7`，与用户看到的旧窗口一致。此前“你打开了旧EXE”的解释不完整，Setup本身也会主动打开旧EXE。

上一轮验证脚本显式传入了`--install`，只覆盖强制安装，漏掉已有旧版本时直接双击的常用路径。原验证不能证明这个场景通过；旧证据保留，本轮增加原生升级验证。

## 修复

- 依据Setup是否包含签名`bootstrap.zip`识别完整安装器，已有`current.json`时也执行安装／升级；不需要命令行参数。
- 不含内嵌安装包的普通Launcher继续启动已安装版本，两种行为明确区分。
- 新版健康检查同时核对真实PID、进程存活和预期版本，不能只凭`ready=true`认为安装成功。
- 安装前向本安装目录内的采集工作台发送正常关闭请求，等待它完成保存并退出，最多30s；不强杀。其他目录的工作台仍运行时明确要求先结束并关闭，不静默打开旧版冒充成功。
- 安装包摘要、签名、版本单调检查、失败恢复及原始记录保护保留。同版本Setup可修复安装，旧安装器不能降级覆盖较新版本。
- 版本提升为1.1.4／11004，不覆盖已发布1.1.3的不可变文件。配对文件输入控件继续保持删除，内部身份自动维护。

## 使用

双击[EEGMovement-Setup-1.1.4.exe](../../../outputs/relay_20261023/release/EEGMovement-Setup-1.1.4.exe)。已装1.1.0也会进入升级路径；不需要卸载或删除记录、配置和原厂驱动。

安装目录中的旧工作台会正常关闭保存。如果提示旧工作台还在运行或保存，等待其退出后重新双击；不会强制中止原厂采集。启动后标题应为**v1.1.4**，新桌面入口也指向版本管理启动器。

## 验证

[修复前测试](red.log)复现了双击Setup跳过升级以及健康检查未核对版本；[针对性回归](green.log)16项通过。

本轮新增`verify_windows_setup_doubleclick.py`：在隔离Windows用户数据目录实际安装并运行1.1.0，读取真实窗口标题；然后不传任何参数运行新Setup，核对旧进程正常退出、真实窗口标题与健康回执版本、`current.json`升级、记录目录保留，以及再次双击同版本Setup。不是只执行`--install`或只修改指针的模拟测试。

原生运行、失败恢复、公网发布与下载校验的实际结果分别记录在本目录JSON和日志。软件联调不等于另一台采集电脑或真实EEG验收，SDK和模型未启动。

本轮原生双击验收已通过，见[doubleclick-native.json](doubleclick-native.json)：旧窗口标题实际为v1.1.0，新窗口实际为v1.1.4；旧程序正常退出、记录目录保留、再次双击修复安装均通过。[本机默认安装目录实测](local-installed.json)也从实际1.1.0指针升级至1.1.4，核对了当前进程的真实窗口标题与健康回执。未自动安装到另一台采集电脑。

发布身份见[publication.json](publication.json)，真实签名清单和HTTPS范围下载验证见[update-download-check.json](update-download-check.json)。原生错误更新包拒绝与上一版本恢复另见[install-rollback.json](install-rollback.json)。
