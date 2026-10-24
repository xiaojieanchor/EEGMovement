"""Render committed scientific experiment evidence to Markdown, PNG/PDF figures,
and a Songti PDF. No training, web dashboard replacement, or network access.
"""
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/gnautilus/initial_training_20261023'
NAMES={'bci_iv_2a':'BCI IV-2a','halt':'HaLT 四肢','eegmmidb':'PhysioNet 左右手','openbmi':'OpenBMI 左右手'}
ALGS={'bandpower_lda':'频带功率＋LDA','csp_lda':'CSP＋LDA','cnn_gru':'CNN＋GRU'}
LABELS={'LEFT_HAND_MI':'左手','RIGHT_HAND_MI':'右手','LEFT_LEG_MI':'左腿','RIGHT_LEG_MI':'右腿','BOTH_FEET_MI':'双脚','TONGUE_MI':'舌'}
ORDER=['bci_iv_2a','halt','eegmmidb','openbmi']


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties,fontManager
    font=Path('C:/Windows/Fonts/simsun.ttc');fontManager.addfont(str(font));family=FontProperties(fname=str(font)).get_name()
    plt.rcParams.update({'font.family':family,'axes.unicode_minus':False,'font.size':13.5,'axes.spines.top':False,'axes.spines.right':False,
                         'axes.edgecolor':'#333333','text.color':'#222222','axes.labelcolor':'#222222','xtick.color':'#333333','ytick.color':'#333333','figure.facecolor':'white','axes.facecolor':'white'})
    source=OUT/'analysis-results.json';data=json.loads(source.read_text(encoding='utf-8'));results=data['results']
    assert len(results)==12 and all(r['state']=='completed' for r in results),'Wait for all twelve governed pilot jobs'
    assert not data['failed_jobs'] and not data['quality_errors']
    chosen={reader:next(r for r in results if r['reader']==reader and r['algorithm']=='cnn_gru') for reader in ORDER}
    figures=OUT/'figures';figures.mkdir(exist_ok=True)
    contract=dict(surface='Standalone scientific figures for the project Markdown and printable PDF',renderer='Matplotlib',
        source_json_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),palette='Neutral black/gray only; line styles and open/filled markers distinguish series',
        charts=[dict(id='learning-ba',family='line',question='How does group-macro BA evolve over the fifteen actual epochs?',points=15,series=['training','development'],
                     benchmark='1 / native class count',takeaway='Learning and cross-group transfer vary by public dataset; best development epoch is marked'),
                dict(id='learning-ce',family='line',question='Do actual training and development cross-entropies decline?',points=15,series=['training','development'],
                     takeaway='No synthetic epochs for classical methods; curves use committed neural events'),
                dict(id='method-comparison',family='dot_and_interval',question='Which of the three fixed methods transfers on each native public task?',categories=3,facets=4,
                     fields=['train_group_macro_BA','development_group_macro_BA','development_group_bootstrap_95','group_count','window_count'],
                     takeaway='A neural architecture does not dominate all datasets; intervals are descriptive group variability'),
                dict(id='confusion',family='matrix',question='Which native limbs/classes are confused?',facets=4,
                     fields=['true_class','predicted_class','count','row_fraction'],takeaway='Conditional native-task recall cannot establish continuous VR control')])
    (OUT/'chart-contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    def save(fig,name):
        fig.savefig(figures/(name+'.png'),dpi=180,facecolor='white');fig.savefig(figures/(name+'.pdf'),facecolor='white');plt.close(fig)
    for kind in ['ba','ce']:
        fig,axes=plt.subplots(2,2,figsize=(12,8),layout='constrained')
        fig.suptitle('每轮训练与开发分组平衡准确率' if kind=='ba' else '每轮训练与开发交叉熵',fontsize=17)
        for ax,reader in zip(axes.flat,ORDER):
            row=chosen[reader];h=row['history'];epochs=[v['epoch'] for v in h];n=len(row['manifest']['class_labels']);g=len(row['recomputed_metrics']['development']['groups'])
            assert len(h)==15 and epochs==list(range(1,16))
            ax.set_title(f'{NAMES[reader]} · {n}类 · {g}个开发组',fontsize=14)
            a='training_balanced_accuracy' if kind=='ba' else 'training_loss';b='development_balanced_accuracy' if kind=='ba' else 'development_loss';scale=100 if kind=='ba' else 1
            ax.plot(epochs,[v[a]*scale for v in h],color='#333333',linestyle='-',marker='o',markersize=3,label='训练')
            ax.plot(epochs,[v[b]*scale for v in h],color='#111111',linestyle='--',marker='s',markerfacecolor='white',markersize=3,label='开发')
            ax.axvline(row['manifest']['details']['selected_epoch'],color='#666666',linestyle=':',linewidth=1,label='入选轮次')
            if kind=='ba':ax.axhline(100/n,color='#999999',linestyle='-.',linewidth=1,label='机会水平');ax.set_ylim(0,100)
            else:ax.set_ylim(bottom=0)
            ax.set_xlabel('已提交的实际 epoch');ax.set_ylabel('分组平衡准确率（%）' if kind=='ba' else '未加权交叉熵');ax.set_xlim(1,15);ax.set_xticks([1,3,5,7,9,11,13,15]);ax.grid(axis='y',color='#dddddd',linewidth=.5);ax.legend(fontsize=12.5,loc='best')
        save(fig,'learning-'+kind)
    fig,axes=plt.subplots(2,2,figsize=(12,7),layout='constrained');fig.suptitle('固定方法的训练与开发成绩',fontsize=17)
    for ax,reader in zip(axes.flat,ORDER):
        rows=[next(r for r in results if r['reader']==reader and r['algorithm']==a) for a in ALGS]
        for i,r in enumerate(rows):
            train=r['recomputed_metrics']['training']['group_macro_balanced_accuracy']*100;dev=r['recomputed_metrics']['development']['group_macro_balanced_accuracy']*100;lo,hi=np.array(r['recomputed_metrics']['development']['descriptive_group_bootstrap_95'])*100
            ax.plot([train,dev],[i,i],color='#aaaaaa',linewidth=1);ax.scatter([train],[i],facecolors='white',edgecolors='#333333',marker='o',s=35)
            ax.errorbar([dev],[i],xerr=[[dev-lo],[hi-dev]],fmt='s',color='#111111',capsize=3,markersize=5)
        ax.axvline(100/len(rows[0]['manifest']['class_labels']),color='#777777',linestyle='--',linewidth=1)
        ax.set_title(f"{NAMES[reader]} · {len(rows[0]['recomputed_metrics']['development']['groups'])}个开发组",fontsize=14);ax.set_yticks(range(3),[ALGS[r['algorithm']] for r in rows]);ax.set_xlim(0,100);ax.set_xlabel('分组平衡准确率（%）');ax.invert_yaxis();ax.grid(axis='x',color='#dddddd',linewidth=.5)
    fig.supxlabel('空心圆：训练；实心方块：开发；误差线：开发组重采样95%描述区间\n不含开发选模偏差与随机种子波动',fontsize=12.5)
    save(fig,'method-comparison')
    fig,axes=plt.subplots(2,2,figsize=(11,9),layout='constrained');fig.suptitle('轻量网络的开发集原生类别混淆矩阵',fontsize=16)
    for ax,reader in zip(axes.flat,ORDER):
        metrics=chosen[reader]['recomputed_metrics']['development'];matrix=np.array(metrics['confusion_matrix']);labels=metrics['class_order'];size=len(labels);percent=matrix/np.maximum(matrix.sum(1,keepdims=True),1)*100
        ax.set_xlim(-.5,size-.5);ax.set_ylim(size-.5,-.5);ax.set_aspect('equal');ax.set_xticks(range(size),[LABELS.get(v,v) for v in labels]);ax.set_yticks(range(size),[LABELS.get(v,v) for v in labels]);ax.set_xlabel('预测类别');ax.set_ylabel('原生真实类别');ax.set_title(f"{NAMES[reader]} · {metrics['windows']}个合格试次",fontsize=14)
        for i in range(size):
            for j in range(size):ax.text(j,i,f'{matrix[i,j]}\n{percent[i,j]:.1f}%',ha='center',va='center',fontsize=12.5,fontweight='bold' if i==j else 'normal')
        for edge in np.arange(-.5,size,.5):
            if float(edge+.5).is_integer():ax.axhline(edge,color='#cccccc',linewidth=.5);ax.axvline(edge,color='#cccccc',linewidth=.5)
    fig.supxlabel('每格：试次数及该真实类别内比例。\n仅已标注且质量合格的闭集试次；无控制与连续停止未由此验证。',fontsize=12.5)
    save(fig,'confusion')
    lines=['# 公共数据质量评估与初步训练验证','',f"结果快照UTC：{data['updated_utc']}。使用新私有服务器172.25.56.83及指定A100；全部训练任务经来源／协议／入口检查后启动。",'',
        '本轮完成四组原生运动想象方法对照，每组频带功率＋LDA、CSP＋LDA、轻量CNN＋GRU各一次，共12个模型。神经网络最多15轮、seed=17，每轮同时评估训练及开发数据；按开发分组平衡准确率选模型，相同成绩取最早轮次。所有模型作为公开方法验证收录，不直接赋予个人导航或VR动作语义。','',
        '## 先进行的数据核查','',
        '检查来源SHA256、单位、通道身份、标记及试次边界、原生类别、完整会话分组、非有限值、平线、幅度、RMS及50/60Hz工频比例。质量阈值在看开发模型分数前固定：绝对值1000µV、峰峰值500µV、去均值RMS100µV、最低峰峰值0.1µV、工频功率比例0.5。任一选定分析通道不合格即剔除该候选窗；每个文件每类至少保留3个试次才纳入。它们是本轮公共数据的粗大电气异常规则，不是g.Nautilus实机阈值或接触阻抗验收。','',
        '|数据|纳入文件|训练试次|开发试次|训练／开发组|候选试次留存|','|---|---:|---:|---:|---:|---:|']
    for reader in ORDER:
        q=data['quality'][reader];lines.append(f"|{NAMES[reader]}|{q['admitted_recordings']}|{q['roles']['train']}|{q['roles']['development']}|{q['groups']['train']} / {q['groups']['development']}|{100*q['fitting_coverage']:.2f}%（{q['fitting_window_count']}/{q['source_trial_count']}）|")
    lines+=['',
        '上述分母是对应试验的候选原生任务试次；不包含未知段，也不把静息、执行任务或不支持的采样身份补成同一类。PhysioNet分母限160Hz左右手想象run，OpenBMI分母限预先选择的前12人两会话EEG_MI_train分区。','',
        '质量剔除的具体原因如下。同一窗可能同时命中多个原因，各项不能相加作为独立剔除数；PhysioNet另有整份记录的每类最低支持量检查，合格窗仍可能因整份记录未通过而不纳入。','',
        '|数据|命中的原因与次数（可重叠）|通过逐窗规则但未纳入拟合|','|---|---|---:|']
    reasons={'provider_marked_artifact':'原提供方伪迹','line':'工频比例','rms':'RMS','ptp':'峰峰值','absolute':'绝对幅度','flat':'平线','nonfinite':'非有限值','task_or_recording_boundary':'任务边界'}
    for reader in ORDER:
        q=data['quality'][reader];counts='；'.join(f'{reasons.get(k,k)} {v}' for k,v in q['rejection_reasons'].items())
        unused=sum(q['gross_retained_trial_classes'].values())-q['fitting_window_count']
        lines.append(f"|{NAMES[reader]}|{counts or '无'}|{unused}|")
    lines+=['',
        '- BCI IV-2a：实际读取器原先把3路EOG也列为EEG，现已明确分开，仅用官方蒙太奇的22路EEG。标签与已验证原始标签ZIP及GDF事件顺序交叉核对；1023专家伪迹标记的试次不进入主成绩。固定从提示后1.5秒开始取1.5秒窗。T会话训练、E会话开发，不以此声称复现官方连续kappa评测。',
        '- HaLT：保留原生左右手、左右腿。去掉A1/A2及末路X5同步信号；同步信号不是EEG。分析7个实际存在的运动／顶区位置，窗为提示后0.25–1.00秒；每个参与者最近原生日期用于开发，其余日期用于训练，单日参与者保留而不混入本轮。原始数据为µV，不按分辨率再缩放。',
        '- PhysioNet：全部EDF进行数值粗查，训练只用run4/8/12的左右手想象及160Hz头文件。S088、S092、S100各12份头文件标128Hz的记录另列，不强行当160Hz。选取实际存在的21个FC/C/CP位置；1–80号为训练候选，81–109号为开发候选，质控后实际组数见表。无控制和实动数据不改成左右手监督。',
        '- OpenBMI：初步训练固定用前12人两次会话，每次只用原EEG_MI_train分区；EEG_MI_test不参与拟合或选模。真实class表1=右手、2=左手。通过smt和连续x的逐值对照确认原始t的索引起点。只用20个实际运动区通道；1000→250Hz采用有限历史窗内8阶100Hz因果低通后取3::4，不读取窗末之后的样本。保留EMG及其余通道供后续对照，不进入该分类器。',
        '- BCI IV-1本轮暂不拟合：100Hz数据与1000Hz真值需要单独时间对齐，人工数据也须独立协议。EEGEyeNet目前只有单人示例，原整库不可下载，暂不据此建立眼动＋四肢融合模型。','',
        f"完整来源核查记录数：{data['quality_assessed_files']}；按来源计数：{json.dumps(data['quality_source_counts'],ensure_ascii=False)}。全量数值评估完成状态：{'已完成' if data['quality_complete'] else '仍在进行，未冒称全量完成'}。",'',
        '以下范围来自原始连续数据的全部被检查样本，包含非任务、断点及伪迹段，不能当成保留试次的幅度范围，也不能说明全库都可用于拟合。HaLT单日参与者的一份记录只做来源／分组保留登记，未数值检查。','',
        '|来源|核查／实际数值检查文件|数值分区|NaN／Inf数|原始观测极值（µV）|','|---|---:|---:|---:|---:|']
    native={'bci_iv_2a':'bci_iv_2a','halt':'kaya2018_halt','eegmmidb':'eegmmidb','openbmi':'openbmi_mi'}
    for reader in ORDER:
        g=data['gross_source_summary'][native[reader]]
        lines.append(f"|{NAMES[reader]}|{g['source_records']} / {g['numeric_source_records']}|{g['numeric_partitions']}|{g['nonfinite_values']}|{g['raw_observed_min_uv']:.1f}～{g['raw_observed_max_uv']:.1f}|")
    lines+=['',
        'BCI文件全局负极值、OpenBMI量程附近的极值需要保留为来源质量观察；本轮只拟合经逐试次粗大异常筛选和原生边界检查后保留的数据，不把非有限值为零误写成无伪迹。','',
        '训练／开发来源和原生组均不交叉；另对准备后的数值窗做完全相同内容检查，四组跨角色重复均为0。日期／实际摘戴未知处保持未知，使用经核实的原生日期、会话或受试者分组，不编造佩戴ID。该数值重复检查是训练后额外复核，训练前已有完整来源与组隔离检查。','',
        '## 实际训练方法与参数','',
        '- 频带功率：4阶Butterworth因果SOS分别提取8–12、16–24Hz，各通道窗内均方功率取log10；训练组均值／标准差归一化，固定0.1协方差收缩LDA及等先验。该结构对应厂商Bandpower模板的可见思路，未调用不可见的.p或MEX实现。',
        '- CSP：8–30Hz因果SOS，逐试次协方差除以迹；每类对其余类OVR，均值协方差固定0.1收缩，广义特征分解后每类取两端各2个空间分量。投影方差除以分量方差总和、取log10，再用训练组归一化及相同LDA。二分类的两次OVR含对称冗余，本轮不据此宣称额外独立信息。',
        '- 轻量CNN＋GRU：原有限窗按训练组逐通道均值／标准差处理；8个长度17的左侧填充时间卷积，16个分组空间卷积，ELU、长度4平均池化、单向GRU（隐藏32）和线性头。AdamW学习率0.001、weight decay 0.01、batch 32、梯度范数裁剪1、FP32、seed 17、固定15轮。不是完整EEGNet，也不是恢复旧JEPA。',
        '- 三类方法都只读各自窗口历史；滤波及网络状态按有限窗口重建，离线导出与运行一致。本轮没有把有限窗口方案称为跨窗持久状态模型，没有训练眼动或后融合分支。','',
        '## 训练和开发结果','',
        '主指标为合格试次上、按完整开发组先算再宏平均的平衡准确率。以下区间对开发组重采样2000次；它反映本次组间波动，不包含单种子波动、开发选模偏差，也不是个人独立新会话验收。','',
        '|数据|算法|训练BA|开发BA|开发组描述95%区间|入选轮次|','|---|---|---:|---:|---|---:|']
    for reader in ORDER:
        for alg in ALGS:
            r=next(v for v in results if v['reader']==reader and v['algorithm']==alg);t=r['recomputed_metrics']['training'];v=r['recomputed_metrics']['development'];lo,hi=v['descriptive_group_bootstrap_95'];ep=r['manifest']['details'].get('selected_epoch','—')
            lines.append(f"|{NAMES[reader]}|{ALGS[alg]}|{t['group_macro_balanced_accuracy']*100:.2f}%|{v['group_macro_balanced_accuracy']*100:.2f}%|{lo*100:.2f}–{hi*100:.2f}%|{ep}|")
    lines+=['','![方法对照](figures/method-comparison.png)','',
        '## 初步判断','',
        '公开库之间没有混训。CSP的空间滤波和LDA标准化只用训练数据拟合，网络均值／标准差也只来自训练数据。后续开发分数用于明确标记的模型选择，不作为独立测试。每个候选原生试次只取一个固定窗，未把密集重叠窗当成独立样本。','',
        '当前结果不支持“轻量深度模型在所有任务上都优于CSP”。BCI IV-2a中应保留CSP作为强基线；当前原始有限窗CNN＋GRU的开发结果接近机会水平。HaLT和PhysioNet上的相对优势仍需跨种子、个人数据及干扰对照确认。不能根据这些数字承诺左右腿或自然运动意图已可稳定驱动VR。','',
        '本轮CSP是跨参与者合并拟合的固定OVR正则化基线，不等同每人单独优化的厂商模板或竞赛最优实现。网络只有15轮固定预算，未做超参数搜索。更多轮数、更多参数或训练内分数上升，不自动证明跨会话收益。','',
        '头皮运动想象数据可能包含视觉提示、眼动及肌电成分。排除EOG输入／同步通道和粗大异常不能证明剩余判别只来自神经运动意图。后续个人采集仍须中央注视MI、仅眼动、融合和头动对照；无控制、拒识、持续保持、停止与误启动需另验。','',
        '## 边训练边评估与产物核验','',
        '每轮提交真实训练／开发交叉熵、分组BA、梯度范数和四个网络模块的首批梯度。所有入选权重从可恢复checkpoint或最终导出提交；以开发集选出的同一模型同时报告训练和开发成绩，不用“训练最高轮次”替换。', '',
        '12个产物均通过manifest／权重摘要和完整开发集的导出前后预测一致性核对；最终指标又从保存的逐试次预测重新计算。任务只有在模型库登记成功后才为completed。没有自动选择模型、启动实时推理或允许动作；公开原生任务模型runtime_accepted=false。','',
        '中枢原页面结构和样式保留，只补充训练区的数据身份与曲线。刷新训练页面可查看真实任务、原生组、固定窗口、训练／开发曲线；模型库能看到本轮经典分类器和神经网络产物。','',
        '![每轮BA](figures/learning-ba.png)','','![每轮交叉熵](figures/learning-ce.png)','','![类别混淆](figures/confusion.png)','',
        '## 证据与后续使用','',
        '- `analysis-results.json`：重算指标、完整任务事件、类别混淆、分组区间、源码／协议／产物身份。',
        '- `study-plan.json`：在看开发分数前锁定的规则；`jobs.json`：12条提交及实际preflight结果。',
        '- 服务器 `studies/public-mi-pilot-20261023-ef5ec07d-6765fc23/`：质量进度、完成状态和分析；`data/public_prepared/`：逐原始源的质量回执和不可变准备窗；`logs/`：按任务保存的训练、checkpoint、逐试次预测和来源ID；`models/`：统一登记的JSON／NPZ产物。',
        '- [原始公共下载与许可记录](../public_download_20261023/公开数据下载清单与状态.md)。',
        '- 方法及数据依据：[BCI IV-2a原始说明](https://www.bbci.de/competition/iv/desc_2a.pdf)、[HaLT原始论文](https://www.nature.com/articles/sdata2018211)、[PhysioNet官方页](https://physionet.org/content/eegmmidb/1.0.0/)、[OpenBMI原始论文](https://doi.org/10.1093/gigascience/giz002)。','',
        '下一步优先用个人新日期／新摘戴数据，按相同质量与完整组原则重训和验证。先比较个人CSP／频带功率与轻量网络，再单独验证眼动与MI后融合；公共库的不同通道、参考和原生任务不能直接补成32路导航监督。','',
        '## 模型库定位索引','',
        '在原训练页下方按算法卡片选择以下版本；这里只列前12位，完整SHA256和输入合同见analysis-results.json及evidence中的逐模型manifest。模型选择和启动动作均为独立手动操作。','',
        '|数据|算法|模型版本前12位|任务前10位|','|---|---|---|---|']
    for reader in ORDER:
        for alg in ALGS:
            r=next(v for v in results if v['reader']==reader and v['algorithm']==alg)
            lines.append(f"|{NAMES[reader]}|{ALGS[alg]}|{r['model_id'][:12]}|{r['job_id'][:10]}|")
    lines.append('')
    markdown='\n'.join(lines);(OUT/'公共数据质量评估与初步训练验证.md').write_text(markdown,encoding='utf-8')
    # Songti, black/white, unshaded three-line tables. Scientific figures remain
    # standalone artifacts as well as being embedded in the printable record.
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,CondPageBreak,KeepTogether
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    pdfmetrics.registerFont(TTFont('Songti',str(font),subfontIndex=0))
    body=ParagraphStyle('body',fontName='Songti',fontSize=10.5,leading=17,spaceAfter=7,wordWrap='CJK')
    heading=ParagraphStyle('heading',parent=body,fontSize=14,leading=21,spaceBefore=12,spaceAfter=8,keepWithNext=True)
    title=ParagraphStyle('title',parent=heading,fontSize=18,leading=25,alignment=TA_CENTER)
    small=ParagraphStyle('small',parent=body,fontSize=8,leading=12)
    story=[];i=0;rows=markdown.splitlines();width=A4[0]-84
    while i<len(rows):
        line=rows[i]
        if not line: i+=1;continue
        if line.startswith('!['):
            target=line.split('](')[1][:-1];path=OUT/target
            from PIL import Image as PILImage
            with PILImage.open(path) as im:w,h=im.size
            display_height=min(width*h/w,650);display_width=display_height*w/h
            story.extend([CondPageBreak(display_height+10),Image(str(path),width=display_width,height=display_height),Spacer(1,10)])
        elif line.startswith('|'):
            values=[]
            while i<len(rows) and rows[i].startswith('|'):
                row=[c.strip() for c in rows[i].strip('|').split('|')]
                if not all(set(c)<=set('-:') for c in row):values.append([Paragraph(html.escape(c),small) for c in row])
                i+=1
            cols=len(values[0]);table=Table(values,colWidths=[width/cols]*cols,repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('FONTNAME',(0,0),(-1,-1),'Songti'),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEABOVE',(0,0),(-1,0),.9,colors.black),('LINEBELOW',(0,0),(-1,0),.5,colors.black),('LINEBELOW',(0,-1),(-1,-1),.9,colors.black)]));story.extend([table,Spacer(1,8)]);continue
        else:
            import re
            if line=='## 模型库定位索引':story.append(CondPageBreak(420))
            clean=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'\1',line).replace('`','')
            style=title if line.startswith('# ') else heading if line.startswith('## ') else body
            if clean.startswith('## '):clean=clean[3:]
            elif clean.startswith('# '):clean=clean[2:]
            story.append(Paragraph(html.escape(clean),style))
        i+=1
    target=OUT/'公共数据质量评估与初步训练验证.pdf'
    doc=SimpleDocTemplate(str(target),pagesize=A4,rightMargin=42,leftMargin=42,topMargin=42,bottomMargin=42,title='公共数据质量评估与初步训练验证',author='EEGMovement')
    def footer(canvas,doc):canvas.setFont('Songti',9);canvas.drawCentredString(A4[0]/2,23,'EEGMovement · '+str(doc.page))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    print(json.dumps(dict(pdf=str(target),models=len(results),figures=4),ensure_ascii=True))


if __name__=='__main__':main()
