"""Render traceable cards from every unique added model's static evidence."""
from pathlib import Path
import json,re,xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
E=HERE/'evidence'

def text(s):return str(s or '').replace('\n',' ').replace('|','／').strip()
def short(s,n=210):
    s=text(s);return s if len(s)<=n else s[:n]+'…（完整值见结构JSON）'
def params(b):
    p=dict(b.get('instance_parameters',{}))
    for k in ['Gain','Value','Operator','Inputs','Numerator','Denominator','SampleTime','Parameters']:
        if k in b['properties']:p.setdefault(k,b['properties'][k])
    for raw in b.get('masks',[]):
        try:
            t=ET.fromstring(raw)
            for m in t.findall('.//MaskParameter'):
                v=m.find('Value')
                if v is not None:p[m.get('Name','')]=''.join(v.itertext())
        except ET.ParseError:pass
    return {k:v for k,v in p.items() if k!='ContentPreviewEnabled'}

def interpretation(m):
    n=Path(m['path']).stem.lower();c=m['category'];links=' '.join(m['library_links']).lower();names=' '.join(text(b['name']) for b in m['blocks']).lower()
    if 'motorimagerycsp_threeclass' in n:return ('三分类提示运动想象；三组CSP各选首2末2行，12维方差→归一化→log10→分类与反馈。','优先借鉴空间投影／特征契约，重做REST／UNKNOWN和会话独立验证；模板不直接输出导航意图。')
    if 'motorimagerycsp_twoclass' in n:return ('二分类提示运动想象；W_CSP首2末2行、4个375样本方差窗，归一化log10后进入Apply Classifier。','优先借鉴CSP＋线性基线；采样率变化要重新解释375样本窗口，并锁定权重、滤波、通道顺序。')
    if n=='motorimagerybandpower':return ('四路带通功率→log10→WV加权和＋偏置→反馈；8–12／16–24Hz各两路，4阶，约1秒功率窗。','适合作为个人运动想象的可解释基线；WV零初始化不是训练结果，判别距离不当速度或概率。')
    if 'lsl' in n:return ('LSL流发送／接收接口或示例，字段含流名／类型、通道数和采样率；内部S-Function依赖另查。','借鉴显式流描述；当前项目使用独立TLS链路，LSL保留为后续互操作，示例参数不作为设备事实。')
    if 'udp' in n:return ('UDP接口或示例，发送编码、源／目的端口与接收通道数、采样周期、迟到包策略可见。','借鉴迟到包处理和批量边界；厂商UDP示例没有证明本项目所需身份、持久化与NAT正确性。')
    if 'signalquality' in n or 'sqcheck' in n:return ('幅值／标准差质量检查与显示；窗口、阈值、单位枚举由Mask传入gSQcheck。','借鉴质量判定与显示分层；示例阈值不代替实际32路湿电极质量标准，设备有效性不等于阻抗。')
    if 'preprocessing'==n or 'filterdemo' in n:return ('数字滤波、均方、移动平均或其频谱显示示例；参数、系数与状态由块和S-Function提供。','滤波由原厂配置；未来算法派生必须避免重复滤波，并记录因果状态、窗口和断点重置。')
    if 'transformation'==n or any(k in n for k in ['selectchannels','selectevents','trigger','decoder']):return ('事件位解码、通道／事件选择、预触发窗口和数据变换；实际功能由所含模块确定。','借鉴物理通道身份与事件样本区间；被裁剪／替换或未标注段不能补成REST或有效训练数据。')
    if 'eog' in n:return ('EOG阈值／选择校准与控制演示；有在线与回放变体，模式由界面Mask和StartFcn控制。','保留眼动的方向极性及使用者意图；若沿用眼动辅助需区分EOG来源和头皮前额EEG，校准不授予动作资格。')
    if 'emg' in n and 'tms' not in n:return ('EMG阈值、选择或二维控制；存在校准模式与外部启动／控制接口。','可作辅助输入或干扰对照；肌电控制不能汇报为纯脑运动意图识别。')
    if 'hybrid' in n:return ('SSVEP Minimum Energy／分类分支与P300范式／处理分支组合，含median和模式／选择控制。','借鉴输入来源与模式控制权分离；刺激依赖、融合触发和误启动须独立测量，不能当自主自然意图。')
    if 'hyperscanning_mi' in n:return ('多被试运动想象范式、方差、分类及记录；跨设备／被试同步要求另查。','只借鉴训练／反馈与记录分工；本项目单人32路不需要复制多被试硬件与标签结构。')
    if 'ssvep' in n:return ('刺激频率相关的能量／分类与反馈链；Delay Line、Median、Minimum Energy等会引入历史支持。','保留为刺激辅助／菜单研究；不把输出间隔或刺激分类等同500ms自然意图识别。')
    if 'p300' in n or 'speller' in n or 'aep' in n or 'assr' in n:return ('刺激范式、触发、目标／非目标响应处理或诱发电位演示；刺激或外部硬件是方法的一部分。','可借鉴事件、基线、平均和范式记录；不直接替代自然导航意图；触觉／音频附件另验。')
    if any(k in n for k in ['tms','ccep','sep_','switching','fes','recoverix','spike']):return ('刺激、诱发电位、侵入式信号或FES闭环相关结构；参数仅描述模板，不构成当前硬件支持。','仅借鉴事件、有效帧、反馈和拒绝逻辑；本轮不启动刺激，不移植专用硬件动作。')
    if 'scope' in n or 'visualization' in n:return ('信号、阈值、数值、空间插值或统计显示；显示缓冲与更新参数不等于采样时刻。','借鉴数据／质量／呈现分层；图形效果不作为信号真实性或分类准确率证据。')
    if 'rereferenc' in n or 'sourcederivation' in n:return ('重参考或源导联的矩阵运算／配置，实际通道映射取决于输入定义。','只在确认参考、地、物理通道及原厂CAR后设计派生，避免重复参考和维度错配。')
    return ('本载体提供通用库、显示、标记、外设或范式示例；下方以实际块、参数、连线和依赖界定可见功能。','按项目实际需要借鉴接口分工；文件存在不表示附件、许可证或运行兼容已确认。')

def main():
    ms=json.loads((E/'model_index_unique.json').read_text('utf-8'));new=[m for m in ms if m['new_content']]
    out=['# 新增模型结构与迁移分析卡片','', '2026年10月22日｜补齐材料静态复核｜新增M085–M170','',
    '## 1. 阅读口径','', '本附录分析86个新增唯一内容模型。全部244个物理模型对应170个内容ID；原M001–M084的说明仍见首轮报告，原编号未重排。卡片中的连线和参数来自XML／MDL，不执行回调；结构推导与已运行实现分开。每份完整结构保留全部块、连线、Mask、InstanceData、库引用、S-Function和回调，正文列出便于审查的关键片段。', '',
    '相同模型的多个路径见[物理清单](evidence/model_index_physical.csv)；完整SHA见[唯一清单](evidence/model_index_unique.csv)。本次新增了Reference块InstanceData的独立记录，避免漏掉继承库实例的滤波／窗口参数。', '',
    '## 2. 重点算法与基础库的解释边界','',
    'M147普通Bandpower、M148三分类CSP、M149二分类CSP是本轮最直接相关的新主模板。M097预处理、M100质量、M111变换、M087 LSL及M114 UDP补齐底层参考。受保护实现、bdmxdata中的工作区／分类器对象、MATLAB运行时解析及许可证仍有证据边界。原厂样例运行结果、训练内分数和本项目独立实测不混为一谈。','']
    coverage=[]
    for row in new:
        m=json.loads((E/'models'/row['id']/'structure.json').read_text('utf-8'));code=m['id'];name=Path(m['path']).name;purpose,advice=interpretation(m)
        out += [f'## {code}. {name}','',f'**用途与采用建议：** {purpose} {advice}','',f'**证据：**[{code}完整结构](evidence/models/{code}/structure.json)；分类为{text(m["category"])}；{len(m["blocks"])}块、{len(m["lines"])}连线、{len(m["callbacks"])}条回调记录。仅静态解析，未运行。', '']
        src=[text(b['name']) for b in m['blocks'] if b['type'] in ['Inport','FromWorkspace','FromFile','Constant','SignalGenerator'] or 'DataAcquisition/' in b['properties'].get('SourceBlock','')]
        dst=[text(b['name']) for b in m['blocks'] if b['type'] in ['Outport','ToWorkspace','ToFile','Scope'] or any(x in b['properties'].get('SourceBlock','') for x in ['Save Data Stream','Visualization/'])]
        out += ['**输入／输出：** 可见输入端或源块为'+('、'.join(src[:10]) if src else '库端口／外部S-Function，需结合具体调用实例')+'；可见输出端或汇块为'+('、'.join(dst[:10]) if dst else '库端口／外部S-Function，当前载体未独立声明数据单位')+'。列数、单位和采样身份以实际采集配置为准。','']
        bymember={}
        for b in m['blocks']:bymember.setdefault(b['member'],{})[str(b['sid'])]=text(b['name'])
        edges=[]
        for l in m['lines']:
            nodes=bymember.get(l['member'],{});s=l.get('source') or ''
            for d in l.get('destinations',[]):
                sn=nodes.get(s.split('#')[0]);dn=nodes.get(str(d).split('#')[0])
                if sn and dn:edges.append(f'{sn}({s}) → {dn}({d})')
        out += ['**逐级数据流：** 以下为实际连线片段，不把库中彼此独立的示例块拼成一个完整算法。','']
        if edges:out += ['- '+e for e in edges[:7]]
        else:out += ['- 此载体为单块／接口库，内部连线不能在本层解释完整处理链；见被调用S-Function或对应使用实例。']
        out+=['']
        ps=[]
        for b in m['blocks']:
            p=params(b)
            if p:ps.append((b,p))
        out += ['**实际参数与数学操作：** ','']
        if ps:
            out+=['| 块SID／名称 | 保存值 | 解释边界 |','|---|---|---|']
            # Keep all functional overrides, representative generic constants capped for readability.
            important=[x for x in ps if x[0].get('instance_parameters') or any(k in text(x[0]['name']).lower() for k in ['csp','variance','bandpower','filter','classifier','moving','mean','quality','trigger'])]
            chosen=important+ [x for x in ps if x not in important]
            for b,p in chosen[:18]:
                val='；'.join(f'{k}={short(v,130)}' for k,v in p.items())
                out.append(f'| {text(b["sid"])}/{text(b["name"])} | {short(val,480)} | 保存参数；表达式未求值，内部数值／状态需运行验证 |')
            if len(chosen)>18:out+=['',f'另有{len(chosen)-18}个含参数块完整保存在结构JSON；长表达式不在表内截断后作为精确常量使用。']
        else:out+=['当前层未保存可直接解释的参数覆盖；不能把库默认值、用户数据引用或外部工作区变量猜成实际已用常量。']
        out+=['','**训练与在线差异：** 含分类器的在线模型依赖训练后空间矩阵／权重／参数；当前结构不证明训练已经完成。离线变体用回放源验证处理链，不能验证无线、真实电极或新佩戴效果。没有训练模块的接口／显示库不承担模型拟合。','']
        deps=[]
        for fn,paths in m.get('dependency_candidates',{}).items():
            suffixes=sorted({Path(p).suffix for p in paths});deps.append(f'{fn}：'+('/'.join(suffixes) if suffixes else '当前材料未解析同名文件')+f'，候选{len(paths)}处')
        out += ['**依赖与局限：** '+('；'.join(deps[:10]) if deps else '本层主要通过库引用或内置块实现，完整库候选在结构JSON登记。')+'。同名候选不是MATLAB搜索路径已解析成功；.p／MEX／DLL仅可确认接口，不能宣称读到内部实现。','']
        callbacks=[c for c in m['callbacks'] if c['name'] in ['PreLoadFcn','LoadFcn','InitFcn','StartFcn','StopFcn','OpenFcn','MaskInitialization']]
        if callbacks:out += ['**回调检查：** '+'；'.join(c['name']+'='+short(c['text'],180) for c in callbacks[:3])+'。仅记录，没有执行。','']
        out += ['**项目适配结论：** '+advice+' 实际32路布局、原厂配置、无控制状态、断点和接收端停止规则须独立验证。','']
        coverage.append({'id':code,'card':True,'input_output':True,'flow_examples':len(edges),'parameter_blocks':len(ps),'dependencies':len(deps),'status':'STATIC_CARD_COMPLETE_NOT_RUNTIME_TESTED'})
    (HERE/'新增模型结构与迁移分析卡片_20261022.md').write_text('\n'.join(out),encoding='utf-8')
    (E/'model_card_coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2),encoding='utf-8')
    print('cards',len(coverage),'chars',sum(len(x) for x in out))

if __name__=='__main__':main()
