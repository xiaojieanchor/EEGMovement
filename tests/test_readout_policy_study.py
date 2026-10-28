import contextlib
import numpy as np

def test_frozen_score_comparison_loads_manifest_parent_and_preserves_source(tmp_path,monkeypatch):
    from tests.test_intent_repair import repaired_model
    from tests.test_eye_evidence import CONFIG
    from gnautilus_ml.spectral_navigation import SpectralHybridModel,RUNTIME
    from gnautilus_ml.artifacts import save_artifact
    from gnautilus_ml.common import atomic,read_json
    from gnautilus_ml.readout_policy_study import run
    old=repaired_model();model=SpectralHybridModel(old.arrays,dict(old.details,spectral_view_mode='absolute',eye_event_config=CONFIG),'cpu');path=save_artifact(tmp_path/'models',model,dict(runtime_schema=RUNTIME,runtime_accepted=False,metrics={}))
    rows=[dict(trial_id=str(i),source_end=25,cache_index=i,navigation_label=i,warming=False,quality_valid=True) for i in range(5)];atomic(tmp_path/'frames.json',rows);prob=np.tile([.6,.1,.1,.1,.1],(5,1));np.savez(tmp_path/'predictions.npz',navigation=prob,navigation_oof=prob,eye=prob,eye_oof=prob,motion_final=np.zeros(5),motion_oof=np.zeros(5));config={k:model.details['intent_policy'][k] for k in ['activity','direction','rest_activity']}
    p=dict(sources=dict(manifest=dict(path=str((path/'manifest.json').relative_to(tmp_path))),frames=dict(path='frames.json'),predictions=dict(path='predictions.npz')),candidates=[config],max_wall_seconds=60,code_sha256={})
    monkeypatch.setattr('gnautilus_ml.governance.training_authority',lambda *a,**k:contextlib.nullcontext());monkeypatch.setattr('gnautilus_ml.governance.compute_device',lambda:'cpu');out=tmp_path/'study';out.mkdir();r=run(tmp_path,tmp_path,p,{},None,out)
    assert not r['admitted'] and r['neural_gradient_updates']==0 and r['model_id']==read_json(path/'manifest.json')['model_id']
