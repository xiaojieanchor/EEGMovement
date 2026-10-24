"""Create explicit, non-runnable study proposals and conservative source registry."""
from pathlib import Path
import json

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[2]

def save(name,value):
    p=BASE/name;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    old=json.loads((BASE.parent/'evidence/device/montages.json').read_text('utf-8'))
    labels={
      'LEFT_HAND_MI':'仅左手动觉想象，不做实际动作',
      'RIGHT_HAND_MI':'仅右手动觉想象，不做实际动作',
      'FOOT_MI':'脚部动觉想象，具体单／双脚在协议锁定',
      'NATURAL_FORWARD':'自然前进意图研究标签，不能由手脚MI换名得到',
      'NATURAL_LEFT_TURN':'自然左转意图研究标签',
      'NATURAL_RIGHT_TURN':'自然右转意图研究标签',
      'GAZE_LEFT_ONLY':'有意左注视且无MI', 'GAZE_RIGHT_ONLY':'有意右注视且无MI',
      'REST_NC':'明确要求无控制的有效时段', 'UNKNOWN':'准备／未标注／意图时点未知',
      'PASSIVE_FEEDBACK':'只看相同画面，不发控制意图',
      'REAL_MOVEMENT':'实际动作观察条件，不冒称MI'}
    stages=[
      {'id':'P0','purpose':'设备与事件真值核对','budget':'API到手后，测试信号和真实静息分开检查','go':'通道映射、缩放、计数、时间、有效性和参考已核定','stop':'任一核心元数据未知／异常，不开始模型实验'},
      {'id':'P1','purpose':'信号可行性与来源对照','budget':'两个日期，每次20–25分钟，可分block休息','go':'跨日期出现可重复生理／任务支持，且眼动／视觉／头动对照能解释主要来源','stop':'无可重复信号先改接触／布局／任务，不扩模型'},
      {'id':'P2','purpose':'基线模型及内层选择','budget':'补足三个训练日期／佩戴；每类先20次5秒尝试，分短block','go':'完整组内嵌套比较CSP／Bandpower，保留所有候选与无收益结果','stop':'分组BA接近置换／信号来源不清，回到P1'},
      {'id':'P3','purpose':'连续NC／拒识与状态参数开发','budget':'另一个开发日期，至少15分钟NC与保持／切换任务','go':'冻结预处理、模型、类别、拒识／后处理与指标定义','stop':'静息误启动不可接受，定位模型／门控／来源，不靠删段抬分'},
      {'id':'P4','purpose':'前瞻新日期新佩戴验收','budget':'至少三个新日期／佩戴，每次含15分钟NC及任务；约45分钟NC总暴露','go':'逐会话报告含拒识成功率、覆盖、误启动、延迟及区间','stop':'结果失败也原样报告；已查看验收不再当全新test'},
      {'id':'P5','purpose':'自然意图与速度研究','budget':'仅P4具可重复支持后另锁协议','go':'自然任务单独训练／评估；速度先平滑策略，真实速度解码另需监督','stop':'不能以MI映射或margin强度声称自然方向／速度已解码'}]
    proposal={'schema':'eegmovement-gnautilus-study-proposal-v1','status':'PROPOSAL_NOT_RUNNABLE','date':'2026-10-22',
      'device':{'confirmed_eeg_channels':32,'confirmed_electrode_type':'wet','channel_port_mapping':None,'sample_rate_hz':None,'native_unit':None,'reference':None,'ground':None,'z_channel':None,'api_version':None},
      'montage':{'recommended':'A','alternative':'B','index_semantics':'research index, not verified hardware port','layouts':old['layouts']},
      'task_semantics':labels,'stages':stages,'split_and_selection':{'source_sha':'exclusive role','date_and_wear':'exclusive role per subject','hyperparameters':'train inner groups only','thresholds':'development only','final_test':'new date/wear, untouched before frozen plan','unknown':'never fill as REST'},
      'statistical_plan':{'unit':'independent session/event, never overlapping frames','nested':'outer leave-date, inner groups require enough independent training dates','permutation':'whole exchangeable task blocks within predeclared strata; rerun fitting/selection for each null','bootstrap':'session-level descriptive intervals; few sessions never automatically high confidence','NC_upper':'Poisson independent-event assumption explicitly reported; also show per-session clustering'},
      'metrics':['BA','per-class recall','conditional_accuracy','coverage_with_reject_warmup_invalid_denominator','success_including_rejection','false_start_events_per_valid_NC_minute','false_motion_duration','onset_hold_reentry_release_latency','update_interval_P50_P95_max','signal_to_execution_and_display_measured_separately'],
      'runtime_contract_proposal':{'built':False,'sample_identity':['recording','epoch','sample_start','sample_end','host_receive_monotonic'],'score_semantics':'margin and calibrated probability separated','stop':'unknown/invalid/reject/warmup/stale/lease/e-stop reset or stop','inference_and_motion':'independently manually enabled','optional_check':'never grants activation permission'},
      'real_training_enabled':False,'real_hardware_accepted':False}
    save('protocol_design/protocol_proposal.json',proposal)
    failures=[
     ('F01','振幅离谱、饱和或全平','单位或缩放重复／偏移／源为测试信号','原始码、厂商缩放、模式、饱和与REF','按API一次缩放；未知单位拒绝，不夹幅伪装修复','原码→单位换算及饱和／未知配置测试'),
     ('F02','工频或低频漂移主导','接触、参考、VR线缆压力、环境','阻抗、原始PSD与VR摘戴配对条件','先修佩戴与参考；滤波配置按分支锁定','同佩戴真实干扰重测，坏段保留'),
     ('F03','断包后短时乱判','计数缺口或重连状态被拼接','counter/validation/epoch与buffer支持','新epoch或清缓存重新预热；冻结CSP可保留相同配置','分包一致／缺包／invalid／epoch回归'),
     ('F04','删坏道后CSP维度错','固定投影与通道集合顺序不匹配','输入通道名与模型manifest逐项比较','停止或切已训练匹配模式，不临时挤压重排','缺道／重排拒绝和恢复'),
     ('F05','CSP／黎曼矩阵奇异','CAR低秩、小样本或重复通道','秩、特征值和协方差条件数','训练折内正则化或固定降维；不偷看test','低秩合成矩阵、零功率、重复名'),
     ('F06','训练好跨日坏','会话漂移、数据泄漏、提示依赖、过拟合','按日期佩戴外层结果及mask/source-only对照','重建分组与任务；减少搜索；补真实独立记录','同源换名／同日佩戴跨role拦截'),
     ('F07','方向跟眼睛而非MI','眼动为主要来源','gaze-only、MI-only、一致／冲突条件','若合法辅助就明确标为混合；单列自然意图能力','保持／回中／中途启动／冲突测试'),
     ('F08','提示一出现就判对','视觉刺激或事件列泄漏','passive-view、同画面不同任务；feature列角色','触发列不进EEG；随机提示；独立无提示测试','包含trigger列的合同与负对照'),
     ('F09','长静息总误动','闭集分类、失准概率或NC支持不足','每分钟事件、持续时间、拒识覆盖、校准曲线','增加代表性NC／拒识与失效识别；开发集锁阈值','含reject/warmup分母、未见NC与眨眼'),
     ('F10','刷新快但停不下','长特征窗／双重平滑／旧证据累计','窗口支持、决策时间、实际执行回执','只一个决策拥有者；停止先于平滑；测响应曲线','重复结果不累计，长gap与stale停止'),
     ('F11','时间或事件错位','主机到达时间冒充采样／跨机时钟差','硬件／软件标记、计数与RTT／显示测量','用样本身份锚定；估计偏差并报告误差','重连、counter回绕、标记边界回归'),
     ('F12','新版模板加载／编译失败','MATLAB版本、许可、P-code/MEX/solver依赖','逐块依赖、release、license与rapid accelerator支持','合法环境做最小加载；保留Python重实现，不盲升级覆盖','小型离线输入输出一致与超时停止'),
     ('F13','MRCP没有低频／提前量虚高','硬件高通或非因果双向滤波','完整滤波级联、真实样本可见范围','独立因果低频分支；无法恢复被删频率','未来扰动不影响过去、阶段延迟实测'),
     ('F14','速度随信心乱跳','margin／概率误作主观速度','score语义、监督目标与控制轨迹','固定速度＋平滑策略；速度解码另立实验','距离拒绝进入概率门、急停优先'),
     ('F15','数据看起来更多但效果虚增','同库镜像／旧test／ME与MI混库','原始SHA、subject/session、版本、native_labels','同源去重、隔离；宁可不使用，不补导航标签','未审库不准入／任务映射拒绝')]
    save('protocol_design/failure_matrix.json',{'schema':'gnautilus-debug-matrix-v1','cases':[dict(id=i,symptom=s,causes=c,evidence=e,repair=r,regression=t) for i,s,c,e,r,t in failures]})
    specs=[
     dict(id='BCI_IV_1',title='BCI Competition IV Dataset 1',url='https://www.bbci.de/competition/iv/desc_1.html',native_tasks=['LEFT_HAND_MI','RIGHT_HAND_MI','FOOT_MI','NC'],subjects='a,b,f,g real; c,d,e artificial excluded from physiological evidence',sessions='calibration + continuous evaluation runs',rate='100 or 1000 Hz distributions; version must be locked',channels='59 EEG; per-file clab',reference=None,license='official citation/use conditions; exact selected payload rights require review',candidate_role='continuous-NC method validation only',risk='cue protocol differs; artificial subjects; no natural navigation labels',overlap='old public manifest requires source re-audit'),
     dict(id='BCI_IV_2a',title='BCI Competition IV 2a / BNCI 2014-001',url='https://www.bbci.de/competition/iv/desc_2a.pdf',native_tasks=['LEFT_HAND_MI','RIGHT_HAND_MI','FEET_MI','TONGUE_MI'],subjects='9',sessions='2 different-day sessions',rate='250 Hz',channels='22 EEG + 3 EOG',reference='left mastoid; right mastoid ground',license='official competition citation/use conditions',candidate_role='SMR/CSP method validation only',risk='cued 4-class, EOG not competition classification features; no arbitrary idle',overlap='BCI IV2a = BNCI2014-001; never count both'),
     dict(id='BCI_IV_2b',title='BCI Competition IV 2b / BNCI 2014-004',url='https://www.bbci.de/competition/iv/desc_2b.pdf',native_tasks=['LEFT_HAND_MI','RIGHT_HAND_MI'],subjects='9',sessions='5; first2 without feedback, last3 with feedback',rate='250 Hz',channels='3 bipolar EEG C3/Cz/C4 + 3 EOG',reference='bipolar EEG derivations differ by subject; EOG left mastoid',license='official competition citation/use conditions',candidate_role='restricted two-class baseline comparison only',risk='bipolar geometry cannot pad into32 monopolar channels; EOG not competition features',overlap='BCI IV2b = BNCI2014-004; never count both'),
     dict(id='EEGMMIDB',title='PhysioNet EEG Motor Movement/Imagery 1.0.0',url='https://physionet.org/content/eegmmidb/1.0.0/',native_tasks=['REST','LEFT_FIST_MI','RIGHT_FIST_MI','BOTH_FISTS_MI','BOTH_FEET_MI','MOTOR_EXECUTION'],subjects='109 in published collection; actual support validated per selected files',sessions='14 runs per subject; not all independent wearing dates',rate='160 Hz',channels='64 EEG; EDF+ annotation separate',reference=None,license='Open Data Commons Attribution License v1.0',candidate_role='run-aware MI vs ME comparison only',risk='T1/T2 meaning changes by run; T0 not arbitrary daily idle; reference pending',overlap='existing S001R01/R04 in legacy archive; reuse identity, not new test'),
     dict(id='OFNER_2017',title='Ofner2017 / BNCI2017-001 / Zenodo834976',url='https://zenodo.org/records/834976',native_tasks=['RIGHT_ELBOW_FLEXION','RIGHT_ELBOW_EXTENSION','RIGHT_SUPINATION','RIGHT_PRONATION','RIGHT_HAND_CLOSE','RIGHT_HAND_OPEN','REST'],subjects='15',sessions='ME day + MI day; day confounded with task domain',rate='512 Hz',channels='61 EEG + 3 EOG + glove/exoskeleton signals',reference='right mastoid; ground AFz',license='CC-BY-4.0 (Zenodo record)',candidate_role='low-frequency method comparison, not natural gait intention',risk='cued sustained arm tasks; exoskeleton support; no unsignalled navigation truth',overlap='Zenodo/BNCI/NEMAR mirror same sources; version mapping required'),
     dict(id='MOBILE_GAIT_2018',title='Full body mobile brain-body imaging during locomotion',url='https://www.nature.com/articles/sdata2018133',native_tasks=['ACTUAL_WALK_LEVEL','ACTUAL_STAIRS','ACTUAL_RAMP'],subjects='10 in descriptor',sessions=None,rate=None,channels='EEG + lower-limb EMG + motion capture; exact files not reviewed',reference=None,license=None,candidate_role='rejected for present training; possible later artifact stress study',risk='actual walking and terrain is not intention-only; movement artifacts and incomplete raw contract',overlap='unknown; do not ingest')]
    rows=[]
    for s in specs:
        s.update(status='rejected_for_current_training' if s['id']=='MOBILE_GAIT_2018' else 'quarantined',target_tasks=s['native_tasks'],
                 metadata_complete=False,source_overlap_checked=False,approved=False,pretraining_admission=False,
                 supervised_training_admission=False,independent_test_admission=False,unit=None,raw_payload_version=None)
        rows.append(s)
    save('datasets/admission_registry.json',{'schema':'gnautilus-public-data-admission-v1','date':'2026-10-22',
      'policy':'No fusion or label rewrite; unknown fields block admission, even pretraining',
      'datasets':rows,'downloaded_new_EEG_payloads':0,'existing_samples':'legacy_sample_references.json'})
    print('protocol proposal, 15 failure cases, 6 conservative dataset records written')

if __name__=='__main__':main()
