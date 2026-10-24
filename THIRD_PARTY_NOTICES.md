# 第三方依赖与未包含材料

- Python、Qt/PySide6、NumPy、SciPy、CFFI、Paramiko、cryptography、websockets、linkedom等依赖通过各自包管理器安装；它们保留各自许可证，本仓库不重新授权这些依赖。
- Unity、XR Interaction Toolkit、OpenXR、UniVRM等依赖按`unity/EEGMovement/Packages/`清单获取，使用须遵守对应许可证。
- `Assets/Characters/AnimationSource`中的Quaternius动画源及其原始CC0许可一起保留。
- Noto Sans CJK字体的许可见`Assets/Resources/Fonts/OFL.txt`。
- 原角色作者**水色の描**的本地利用规约明确写明“改変有無問わず禁止”再分发。角色VRM、Prefab、网格、贴图不进入公开仓库；本地原文件未删除。保留相关角色加载/动画/编辑器代码和利用规约说明。运行带角色的Unity场景前，须在获得允许的本地使用范围内自行导入角色，或自行适配有再分发授权的角色；仓库本身不授予角色许可。
- g.tec原厂安装包、MATLAB/Simulink库、SDK二进制、头文件和原始手册不作为项目自有源码公开。采集电脑须通过原厂渠道安装驱动、g.NEEDaccess与Client API；项目的参与者读取适配代码在`gnautilus_acquisition/sdk.py`。
- 公开EEG/EOG数据库仍受各自许可/使用条款约束。本仓库提供下载、校验与读取程序，不随源码复制数据库。

项目源码公开不表示对其中所有内容授予某一种开源许可证；本次没有替所有者选择新的项目许可证。
