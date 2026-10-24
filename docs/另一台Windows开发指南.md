# 在另一台Windows电脑下载和开发

## 获取源码

安装Git、Python 3.13 x64；前端DOM测试另装Node.js 20以上。克隆仓库后，在项目根目录打开PowerShell。不要把原电脑的`hub/runtime`复制进公开仓库。

```powershell
git clone https://github.com/xiaojieanchor/EEGMovement.git
cd EEGMovement
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/setup_development.py --role acquisition
.\.venv\Scripts\python.exe -m gnautilus_acquisition
```

这会启动采集端UI；不会因为安装依赖而启动EEG、模型或VR动作。没有设备时可以运行`python -m gnautilus_acquisition --smoke-test`。原厂软件负责设备配置和采集启停，本程序只读取共享会话、保存并转发。

## 原厂依赖和公网连接

在采集电脑安装原厂驱动、g.NEEDaccess和Client API。在工作台填写Client API路径；参与者绑定需要原厂C头文件和`x64/GDSClientAPI.dll`，不需要把SDK复制到Git中。不存在、独占或不匹配的原厂共享会话会明确报错。

`gnautilus_acquisition/relay_public.json`仅包含公网地址、公开CA和更新签名公钥，允许随源码分发；它不是设备私钥或人工配对文件。客户端继续按既有自动注册和中枢回传流程运行。首次新电脑注册如需现有中枢处理，请查看工作台分层日志；不能把TLS可达当作已经收到中枢回包。不要把另一台电脑改成第二个同时运行的中枢。

私人身份、缓存、配对状态和原始EEG在各电脑本地生成。Git不会同步这些文件，也不会同步个人录制或训练模型。

## 本机设备诊断（1.1.6）

完整操作见[采集端进度与连接诊断](采集端开发进度与连接诊断_20261024.md)。先在原厂图形 Demo 以非独占模式开始采集，保持其窗口运行。

```powershell
.\.venv\Scripts\python.exe -m gnautilus_acquisition --diagnose
.\.venv\Scripts\python.exe -m gnautilus_acquisition --diagnose --probe-stream
```

发现设备不等于收到样本。诊断未就绪返回退出码2；可双击 scripts/start_acquisition.cmd 启动源码版。旧安装版快捷方式不会自动指向克隆目录。

## 中枢和计算服务开发

仅开发采集端无需私有服务器配置。若需要在此电脑开发中枢：

```powershell
.\.venv\Scripts\python.exe scripts/setup_development.py --role hub
# 用已核对的主机、公钥指纹和GPU身份填写 configs/gnautilus_compute.json
.\.venv\Scripts\python.exe scripts/start_hub.py
```

SSH密码仍在中枢连接时输入，不写入源码或配置。只有获准承担中枢角色且可访问内网的电脑才能连接私有计算服务；公网转发不能成为内网代理。不要直接执行部署、训练或发布脚本来测试UI。实际原厂设备和现场网络、个人识别效果仍需单独验收。

## 测试与构建

```powershell
npm ci
npm test
.\.venv\Scripts\python.exe -m pytest tests/test_navigation_mi_priority.py tests/test_gaze_priority.py tests/test_gnautilus_ml_v1.py tests/test_gnautilus_transport.py -q
.\.venv\Scripts\python.exe -m pip install -r gnautilus_acquisition/requirements-build.txt
.\.venv\Scripts\python.exe scripts/build_gnautilus_acquisition.py
```

测试使用合成夹具，不证明真实EEG效果。包含历史证据检查的脚本需要其未公开的本地证据目录，不属于新电脑默认回归。不能为让测试通过而恢复旧设备训练或模型。

Unity使用`unity/EEGMovement/ProjectSettings/ProjectVersion.txt`指定版本，打开`unity/EEGMovement`项目并恢复Packages依赖。角色文件因原作者禁止再分发而未上传，运行带角色场景需本地另行导入；见[第三方说明](../THIRD_PARTY_NOTICES.md)。历史构建脚本中的`G:/Unity`等路径按本机工具链修改，不能把旧机器路径当作已安装依赖。

## 开发提交

```powershell
git checkout -b feature/acquisition-improvement
git status --short
git add <修改过的源码文件>
git commit -m "Improve acquisition workflow"
git push -u origin feature/acquisition-improvement
```

提交前检查没有录制、SDK、证书私钥、SSH凭据、APK签名密钥或运行配对状态。后续可用`python scripts/audit_public_source.py --staged`检查暂存内容。

## 32路实时检查与16／19路互换

当前电极映射为设备16=Cz、19=C5，JSON零基键15／18；配置及实际验收步骤见[32路实时链路复核](gnautilus/32路实时链路复核与现场验收_20261024.md)。在中枢电脑可运行`python scripts/check_gnautilus_realtime.py`查看当前阻断条件；采集端电脑不应另起第二个中枢来代替此检查。
