"""Assemble reviewed research sections and per-model static evidence cards."""
from pathlib import Path
import collections, json, re, hashlib

BASE=Path(__file__).resolve().parents[1]
EV=BASE/'evidence'
ROOT=BASE.parents[1]

def renumber(text,chapter,title):
    lines=text.splitlines();out=[];sub=0;subsub=0;first=True
    for ln in lines:
        if ln.startswith('## ') and first:
            out.append(f'## {chapter}. {title}');first=False
        elif ln.startswith('## '):
            sub+=1;subsub=0;out.append(f'### {chapter}.{sub} '+re.sub(r'^\d+[.、]?\s*','',ln[3:]))
        elif ln.startswith('### '):
            sub+=1;subsub=0;out.append(f'### {chapter}.{sub} '+re.sub(r'^\d+[.、]?\s*','',ln[4:]))
        elif ln.startswith('#### '):
            subsub+=1;out.append(f'#### {chapter}.{sub}.{subsub} '+re.sub(r'^\d+(?:\.\d+)*[.、]?\s*','',ln[5:]))
        else:out.append(ln)
    return '\n'.join(out)+'\n'

PURPOSE={
'在线分类与决策':('固定分类器应用／类别拒识与时间后处理','优先借鉴接口分层；分数语义、拒识与投票须按新协议验证。'),
'特征提取':('频域、时域、相关或空间投影特征','按具体任务选择；示例数据与滤波频段不能自动迁为自然意图特征。'),
'P300与VR':('刺激同步、P300选择与VR／回放通信','借鉴模式与事件同步；多轮刺激和旧SOCI依赖不直接移入现有Quest运行链。'),
'运动反馈与Avatar':('分类结果向外部Avatar反馈的接口','借鉴反馈分离；厂商受保护接口与旧客户端需另行兼容验证。'),
'EMG/EOG':('眼电／肌电检测或相关触发控制','辅助研究；持续眼位、极性与有意控制必须独立验证。'),
'运动想象频带功率':('两通道双频带功率与线性判别闭环','优先重建小基线；FES支路与保存快照的事件结构不能照搬。'),
'EEG/fNIRS辅助':('多模态同步、范式、分类与反馈','只借鉴分层结构；fNIRS依赖与本地MetaClassifier开关须分开核查。'),
'g.Nautilus设备工具':('加速度校准、设备辅助信号或警告显示','按实际设备／API重建状态信息；校准模板不等于EEG分类器。'),
'外部范式':('外部刺激和数字事件接入','优先借鉴事件分流；输入位、触发编码与时间关系须实测锁定。'),
'记录与回放':('按样本或按帧保存与读取信号','优先借鉴记录与回放分离；回放测试不成为真实控制性能。'),
'采集与数字输入':('放大器／远端采集或数字输入','参考输入与状态组织；真实通道、单位、计数和附加通道仍以实际API确认。'),
'电刺激辅助':('电刺激器或带采集的刺激控制','当前不引入刺激硬件链，只借鉴闭环接口及事件记录思想。'),
'眼动仪辅助':('眼动仪数据采集、同步与范式接口','属于另需眼动仪的资料；不能把存在此模板当作当前VR具备眼位真值。'),
'刺激标记辅助':('刺激呈现、触发或数字IO演示','只有相应硬件可用时验证；软件marker和真实刺激到达分开记录。'),
'多模态辅助':('g.Cube及相关音频／刺激接口','超出当前设备主链，保留依赖登记；不运行这些硬件输出。'),
'音视频辅助':('音频／摄像输入、播放或刺激','仅借鉴事件与反馈时间组织，不能用其演示结果证明EEG解码。'),
'通用库或示例':('通用工具或库入口','根据下列可见块与链接识别功能，按需参考，不将库入口视为已运行算法。')}

def appendices():
    models=json.loads((EV/'model_index.json').read_text('utf-8'))
    lines=['## 附录A. 全部84项模型与模板的分析卡片','',
       '本附录覆盖56个SLTX、27个SLX和1个MDL。每项给出实际结构计数、块／函数线索与采用判断；详细参数、Mask、回调和连线保存在对应结构JSON／XML。SLTX包内模型不是额外计数；库内所有块也不等于示例全部执行。所有项目均未运行。','',
       '| 资料族 | 项数 | 分析状态 |','|---|---|---|']
    for cat,n in collections.Counter(m['category'] for m in models).items():
        lines.append(f'| {cat} | {n} | 结构登记完成；重点算法语义见第3–5章 |')
    lines+=['','目录式重复只保留一个来源索引；本清单保留各文件身份，避免同名版本被误合并。','']
    for m in models:
        data=json.loads((EV/'models'/m['id']/'structure.json').read_text('utf-8'))
        purpose,advice=PURPOSE[m['category']]
        if 'gCutSamples' in m['path']:purpose='按触发或条件裁剪样本；用于事件提取的示例'
        if 'gEP' in m['path']:purpose='诱发响应及基线校正／帧融合的示例'
        if 'gMouse' in m['path']:purpose='信号驱动鼠标接口的示例；本轮不执行操作系统输入'
        top=[b for b in data['blocks'] if b.get('member','').endswith('system_root.xml')]
        if not top:top=data['blocks']
        names=list(dict.fromkeys(str(b.get('name') or b.get('type')) for b in top))
        functions=m['sfunctions'];links=m['library_links']
        outline=' → '.join(n.replace('\n',' ') for n in names[:6])
        fn='、'.join(functions[:5]) or '无直接命名入口；可能经库链接或内部子系统调用'
        link='；'.join(links[:3]) or '未发现直接库引用'
        # Inventory blocks are not automatically a sequence: label them as nodes.
        outline=outline.replace(' → ','；')
        lines +=[f'### {m["id"]} · {Path(m["path"]).name}',
          f'用途与输入输出：{purpose}。分类：{m["category"]}。',
          f'结构证据：{m["block_count"]}个结构块，{m["line_count"]}个连线对象；可见节点为{outline}。节点列举不表示执行顺序，实际拓扑见结构文件。',
          f'依赖线索：{fn}。库链接：{link}。保存的回调项数为{m["callback_count"]}；其具体表达式仅静态记录，不在分析中执行。',
          f'适配判断：{advice}该项的训练／在线角色依其上下文确定，不把库定义当成独立训练器或完整BCI。',
          f'来源：[原始文件]({(ROOT/m["path"]).as_posix()})；[结构证据](evidence/models/{m["id"]}/structure.json)。SHA256：`{m["sha256"]}`。','']
    return '\n'.join(lines)

def main():
    def source_section(folder,chapter,title):
        section=renumber((EV/folder/'section.md').read_text('utf-8'),chapter,title)
        intro=(f'\n本章配套：[来源与证据目录](evidence/{folder}/)；'
               f'[章节原稿](evidence/{folder}/section.md)。本章所说的本目录或局部来源文件，均指该证据目录。\n')
        split=section.index('\n')+1
        return section[:split]+intro+section[split:]
    chunks=[(EV/'root_intro.md').read_text('utf-8'),
       source_section('device',3,'设备能力、32路布局与自然意图的生理依据'),
       source_section('csp',4,'运动想象训练：Bandpower、二分类与三分类CSP'),
       source_section('online',5,'在线判别、辅助范式、质量与VR反馈'),
       (EV/'root_migration.md').read_text('utf-8'),appendices()]
    chunks.append('''## 附录B. 复核入口与来源使用说明

本报告的原始资料未改动。`evidence/source_inventory.json`保存4,188项来源SHA、扩展名统计和重复组；`evidence/model_index.csv`适合逐项筛选；`evidence/models/`保存模型结构。`evidence/device/`、`csp/`、`online/`保存对应章节的逐项来源、核查发现、关键页与源码行索引。模型卡片中的块数是静态结构计数，不是运行覆盖率。

DEV前缀为设备／布局来源，C2／C3／BP为训练批处理源码，G为相应g.BSanalyze接口，ON为在线／VR材料。每类标识在相应章节局部解释；完整路径与SHA在对应JSON索引。跨章节相同原文件引用相同原始身份，不把重复证据当作独立实验。

报告中的两套布局、推荐算法、实验预算和进入条件均为研究建议。手册中的示例准确率、公开论文成绩、静态代码检查、本轮PDF版式检查和未来真实设备验收具有不同身份。记录工具本身不加载MATLAB、不会调用P-code／MEX／DLL，也不触发设备或服务器动作。
''')
    path=BASE/'gNautilus_32通道与Simulink迁移分析_20261022.md'
    content='\n\n'.join(chunks)
    # Use unambiguous ASCII notation for four glyphs absent from embedded SimSun.
    for old,new in [('ᵀ','^T'),('₁','_1'),('₂','_2'),('−','-')]:content=content.replace(old,new)
    path.write_text(content,encoding='utf-8')
    print(json.dumps({'report':path.name,'characters':len(path.read_text('utf-8')),
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'models':84},ensure_ascii=False))

if __name__=='__main__':main()
