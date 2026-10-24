# 共享会话参与者接收实现

## 实现范围

`gnautilus_acquisition/sdk.py`在导入时不加载厂商DLL，不打开硬件。仅在SDK工作进程创建`CffiDriver`时确认Windows x64、GDS服务已运行，按用户选择的SDK目录加载绝对路径`C/x64/GDSClientAPI.dll`。SDK服务固定为`127.0.0.1:50223`，本客户端地址为`127.0.0.1:0`，由系统选择空闲端口。不会调用`GDSServer.dll`或自动启动服务。

绑定从用户安装目录的`GDSClientAPI.h`和`GDSClientAPI_gNautilus.h`生成，移除注释与预处理，仅替换整数字面量宏，不使用`eval`、编译或执行头文件。函数声明采用明确允许清单；设备配置、采集启停、缩放修改、无线信道修改、阻抗测量及角色接管API均未暴露。当前支持的结构要求`GDS_GNAUTILUS_CHANNEL_CONFIGURATION`为40字节、`GDS_GNAUTILUS_CONFIGURATION.Channels`偏移56、整个结构2616字节；不一致则拒绝使用。

随附头文件声明`GDS_Initialize()`与`GDS_Uninitialize()`返回`void`，程序遵守该声明，不将其误当成`GDS_RESULT`读取。其他实际调用读取ErrorCode与ErrorMessage，保留原始错误码。DLL及两份头文件的SHA256登记进流描述，文档版本1.20.01与实际二进制哈希分开。

## 会话权限

先调用`GDS_GetConnectedDevices`，只选择InUse为真的g.Nautilus会话。当前明确支持**单台g.Nautilus**；不擅自合并或拆分原厂多设备组。多台设备可用时要求明确序列号。

`GDS_Connect`使用`openExclusively=FALSE`。原厂会话若独占，原样报告访问被拒绝；若发现与连接之间原厂退出，使本客户端意外成为creator，则立即断开新建空会话，不启动设备。成功后只调用自己的`GDS_StartStreaming/GDS_StopStreaming`，绝不调用设备`StartAcquisition/StopAcquisition`。

原厂软件是否允许共享不能仅从手册证明，必须在采集电脑上确认。没有共享会话时程序有意拒绝接收，不自动接管。

## 元数据与数据排列

读取实际配置、AvailableChannels、SupportedSamplingRates、Scaling、ChannelNames、设备信息及滤波器列表，保留原厂设置，不重新滤波或重新应用缩放。

列排列按照**Enabled且Available的EEG物理通道顺序**，随后依次为已启用的ACC X/Y/Z、Counter、LinkQuality、Battery、DigitalIO和Validation。与`GDS_GetDataInfo`返回宽度必须相等；不按`pygds.IndexAfter`推算，也不截断不符的列。默认EEG显示`CH01`等物理编号；设备固件提供的电极名称另存元数据，不当作实际可调整电极位置。只有明确的`layout.verified=true`及逐启用物理编号映射才标记位置已确认。

EEG单位为uV，ACC为g，Counter为count，Link/Battery为%，Digital为bits，Validation为bool。Scaling中的不可测非有限值以JSON null记录；数据本体NaN/Inf原样保留供上层质量检查，不填零、不伪造样本。

原厂InputSignal必须为Electrode；短路、测试信号和其他输入直接拒绝作为实时EEG。采样率使用设备返回的支持列表；4000Hz为手册示例中的内部阻抗用途，不接入普通EEG流。头文件的500Hz与手册部分512Hz叙述不用于强制设定采样率。

每次读取容量约50ms（ceil(fs/20)），SDK返回0扫描时不生成数据块；部分扫描按实际数量返回。数组为拥有独立内存的little-endian float32矩阵，不保留可被SDK覆盖的缓冲视图。

## 变化、阻塞与退出

注册配置改变、采集停止、创建者退出或改变、服务器退出、强制关闭和采集错误回调。回调只置故障标志，读取前后复核；发生变化即结束当前流，重新开始时创建新的stream_id，不将新配置拼到旧记录中。

SDK在spawn独立进程内运行。主程序入口必须先调用`multiprocessing.freeze_support()`，避免便携EXE重新进入GUI。`SDKWorker.receive(timeout)`返回descriptor、block、error或state消息；最后一次消息时间可供监督器判断缺数据/阻塞。原厂关闭或DLL阻塞超过退出时限时，监督器先terminate再kill，`forced_termination`明确记账。

`SDKWorker.stop(timeout=2,on_message=None)`会保留并返回退出期间排出的消息，调用方必须在关闭日志前提交其中的block；也可传入on_message直接交给写盘器，避免最后若干块静默丢失。强制退出可能丢失尚在原厂内部、尚未交付Python的样本，该边界不可伪称完整保存。

## 本机验证边界

`python -X utf8 -m unittest tests.test_gnautilus_sdk -v`通过19项测试，包括权限、创建者竞态、独占拒绝、动态列、部分/空读取、原始非有限值、配置哈希、读取中生命周期变化、CFFI实布局及指针传参、SDK错误码、进程错误、阻塞限时终止和退出尾块保留。所有输入来自合成数组与测试内注入的模拟驱动；没有导入原厂pygds、加载原厂DLL、访问GDS服务或设备，不构成真实设备验收。

接续：发现流程已增加独立子进程及限时终止；关闭期间写盘回调失败仍确保子进程退出，并保留未提交尾消息计数。测试总数与冻结版本身份以最终verification/delivery.json为准。
