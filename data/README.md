> 当前目标修正：以第一人称行走／转向运动想象为主，眼动辅助；见[任务语义、候选32通道与验证要求](../docs/gnautilus/运动想象主线与眼动辅助_目标修正_20261024.md)。旧眼动优先实验保留作辅助研究，不作为默认主线。

# EEGMovement 数据入口

2026-10-24 眼动优先：新增马耳他大学三组原生EOG研究数据，按完整参与者分组、保留原生轴向/回中/眨眼任务。20个眼动分类器已训练、评估和登记；公开EOG不是本人32路头皮EEG或VR效果验收。完整结果、32路候选布局和采集提示修订见[眼动优先记录](../docs/gnautilus/improvement_20261024/眼动方向优先_公开数据训练与接入修订_20261024.md)。原始数据、质量报告、训练协议和模型仍分层保存在新私有服务器，不与旧设备归档混合。

2026-10-24：在实际来源、单位、标签、完整原生组和电气异常评估后，选定四组公共运动想象派生数据用于初步方法训练。原始公共文件保持不变、全库不自动授予训练身份；纳入范围、剔除原因、训练／开发分组、逐源质量回执和12个模型结果见[初步训练记录](../docs/gnautilus/initial_training_20261023/公共数据质量评估与初步训练验证.md)。派生窗位于服务器`data/public_prepared/`，绑定原始SHA256、准备代码、质量策略及样本边界，不与个人导航数据混存。

2026-10-23 公共数据下载：官方原始文件单独保存在新私有服务器`172.25.56.83:5522`的`/home/sysu/EEGMovement/gnautilus-v1/data/public/`，不与个人记录混存。来源、许可、计划、逐文件SHA256与下载进度见[下载记录](../docs/gnautilus/public_download_20261023/公开数据下载清单与状态.md)。角色保持unassigned，未自动加入训练或改为导航标签。

2026-10-23：新设备模型通路见 `docs/gnautilus/ml_20261023/训练与实时推理使用说明.md`。完整记录经页面核查后上传新私有计算服务器，形成不可变数据集、锁定协议、训练任务及模型登记。原始文件不修改；标签角色、佩戴与质量策略显式补录。当前没有真实新设备训练结果，不恢复旧数据/模型。

当前g.Nautilus数据格式与采集软件已提供，真实设备采集尚待采集电脑验收。新记录按`gnautilus/YYYY-MM-DD/streams/`和`sessions/`组织；前者为传输原始流，后者为中枢主动标签／无标签会话。

每条记录含descriptor.json、raw.f32、blocks.jsonl、events.jsonl及关闭清单closed.json。raw为原厂取数的little-endian float32，EEG为uV，附加信号使用各自单位。块头保留原流序号及样本范围，标签关联stream_id、epoch和源样本位置。

catalog/catalog.json只索引，不授予训练／独立测试资格。未知佩戴保持null，角色unassigned，UNKNOWN不补REST；模型、训练协议和质量规则后续独立验证。旧七路数据及规则不进入新格式。

历史数据位置保持`G:/EEGMovement/Archives/2026-10-22-legacy-neuradock/`，旧catalog和33份未确认关闭状态的快照保持其历史身份，不恢复、不改标签。旧build_data_catalog_v1.py仅支持旧格式；新设备由gnautilus_acquisition.catalog建立独立目录。

详见[API与采集说明](../docs/gnautilus/integration_20261022/Python%20API与独立采集端接入说明_20261022.md)。
