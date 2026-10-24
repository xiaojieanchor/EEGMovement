"""Read-only local-hub live-chain audit. Never activates a model or sends movement."""
import argparse
import datetime
import json
from pathlib import Path
import urllib.request


def assess(status,models,intent,jobs):
    source=status.get('source') or {};descriptor=(status.get('gnautilus') or {}).get('descriptor') or {}
    channels=[c for c in descriptor.get('channels',[]) if c.get('kind')=='EEG'];rows=models.get('models',[])
    loaded=models.get('current_model_id');model=next((v for v in rows if v.get('model_id')==loaded),{})
    contract=model.get('input_contract') or {};checks=[]
    def check(identity,ok,reason):checks.append(dict(id=identity,passed=bool(ok),detail=reason))
    check('fresh_live_source',source.get('fresh') and descriptor.get('source_kind')=='gtec_live','需要新鲜真实来源；补传和回放不算实时')
    check('32_eeg_channels',len(channels)==32,f'实际EEG通道数：{len(channels) if descriptor else "尚未收到描述"}；辅助列另计')
    check('verified_layout_and_units',descriptor.get('layout_verified') is True and channels and all(c.get('unit')=='uV' for c in channels),'实际布局已核对，EEG单位为uV；接触阻抗不据此推断')
    check('private_compute',models.get('remote_fresh') and (jobs.get('server') or {}).get('worker_connected'),'需要私有模型服务的新鲜回包')
    check('model_actually_loaded',bool(model),'已选择的条目不等于实际已加载')
    check('live_navigation_model',model.get('runtime_accepted') and model.get('class_labels')==['REST','FORWARD','BACKWARD','LEFT','RIGHT'],'需要实时导航输入合同和正确类别顺序；不要求效果分数作为启动资格')
    same=bool(model and descriptor and [c['name'] for c in channels]==contract.get('channels') and
        descriptor.get('sampling_rate')==contract.get('sampling_rate') and descriptor.get('reference')==contract.get('reference') and
        descriptor.get('configuration')==contract.get('filter_configuration'))
    check('input_contract_matches',same,'通道顺序、采样率、参考与原厂配置逐项一致；不能只比较32这个数量')
    required=round(model.get('window_s',0)*descriptor.get('sampling_rate',0))
    check('complete_history',required>0 and source.get('continuous_samples',0)>=required,f'需连续历史{required if required else "待模型与设备确定"}样本；逐窗设备有效性及电气QC仍由推理入口检查')
    blockers=[v['id'] for v in checks if not v['passed']];ready=not blockers
    peers=(status.get('vr') or {}).get('clients',[])
    receiver=any(p.get('role')=='unity' and p.get('online') and type(p.get('telemetry_age_ms')) in [int,float] and
        0<=p['telemetry_age_ms']<180 and (p.get('telemetry') or {}).get('motion_schema') in [2,3] for p in peers)
    latest=intent.get('latest') or {};decision=(intent.get('control') or {}).get('decision') or {}
    motion=bool(ready and receiver and intent.get('inference_enabled') and intent.get('fresh') and
                latest.get('eligible') and latest.get('model_id')==loaded and latest.get('source_epoch')==source.get('source_epoch') and
                decision.get('active') and decision.get('model_ready') and decision.get('epoch')==latest.get('source_epoch') and
                (status.get('control') or {}).get('enabled') and
                not (status.get('recording') or {}).get('active') and not (status.get('acquisition') or {}).get('active'))
    candidate=next((v for v in rows if v.get('model_id')==models.get('selected_model_id')),{})
    return dict(schema='gnautilus.realtime_readiness.v1',read_only=True,ready_to_attempt_inference=ready,
        current_motion_conditions_met=motion,hardware_e2e_verified=False,checks=checks,blockers=blockers,
        compatible_unity_receiver_online=receiver,models_total=len(rows),live_contract_models=sum(bool(v.get('runtime_accepted')) for v in rows),
        selected_model=dict(id=candidate.get('model_id'),algorithm=candidate.get('algorithm'),eeg_channels=len(candidate.get('channels',[])),
            sampling_rate=candidate.get('sampling_rate'),runtime_accepted=candidate.get('runtime_accepted')),
        actual_loaded_model=loaded,inference_enabled=bool(intent.get('inference_enabled')),actions_enabled=bool((status.get('control') or {}).get('enabled')),
        model_decision={k:decision.get(k) for k in ['active','model_ready','action_label','reason','decision_id','epoch']},
        note='这是当前状态诊断，不是一次真实EEG到头显运动的验收；本工具不启动采集、推理或动作。')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output');args=parser.parse_args()
    values=[]
    for route in ['/api/status','/api/models','/api/intent/status','/api/training/jobs']:
        with urllib.request.urlopen('http://127.0.0.1:8765'+route,timeout=10) as response:values.append(json.load(response))
    report=assess(*values);report['checked_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    if args.output:
        path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
