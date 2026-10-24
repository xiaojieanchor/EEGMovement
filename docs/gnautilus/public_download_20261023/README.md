# 公共数据下载交付（2026-10-23）

完成UTC：2026-10-23T14:56:02.766834+00:00。新私有服务器`172.25.56.83:5522`，目录`/home/sysu/EEGMovement/gnautilus-v1/data/public`。

已完成3,219个原始包/文件、70,401,856,873字节的下载及独立SHA256复核。原始档案与解包结果留在服务器；本目录仅保存小体积计划、清单、索引、来源和校验回执。

- [完整清单及使用边界](公开数据下载清单与状态.md)
- [完成回执](download-completion.json)与[独立全文件复核](independent-file-verification.json)
- [机器可读数据集清单](public-catalog.json)
- [原生受试者/会话/run索引](recording-index.csv)
- [EEGEyeNet整库可用性检查](diagnostics/eegeyenet-availability.json)

服务端`_sources/20261023/`在本机副本对应`sources/`；其余回执内路径以服务器数据根目录为准。`server-status/`是全部完成后的下载状态快照。四份当前计划为core-plan、physio-plan、openbmi-plan、eye-example-plan，其余计划只保留排障历史。

共1,696条原生记录索引，其中BCI IV-1含6条人工生成记录并已单列身份；不能把全部计作真人EEG。EEGEyeNet整库当前官方下载404，下载的OpenNeuro ds005872仅为EP10单人示例。所有训练角色未分配，未启动训练或恢复旧权重。
