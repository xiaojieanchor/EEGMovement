"""Governed trial-grouped spectral and nonlinear readout comparisons."""
import time,itertools
from pathlib import Path
import numpy as np
from .common import read_json,atomic,digest,file_hash,within
from .navigation_contract import CHANNELS
from .spectral_views import select_view
from .kernel_readout import fit_kernel,kernel_features
from .intent_repair import evidence
from .intent_repair_training import fit_architecture,calibrate,rescale
from .hybrid_training import scores_report
from .navigation_stability_study import replay
from .spectral_navigation import SpectralHybridModel,RUNTIME

SCHEMA='gnautilus.spectral_view_study.v1'

def candidates():
    masks=[dict(name='source8',indices=[CHANNELS.index(c) for c in ['FC6','C2','C4','C6','CPz','CP4','CP6','P4']]),dict(name='motor24',indices=list(range(8,32)))];items=[]
    for mask,view in itertools.product(masks,['absolute','band_ratio','temporal_contrast','combined']):
        for penalty in [.005,.05,.2]:items.append(dict(name=mask['name']+'-'+view+'-linear-'+str(penalty),mask=mask,view=view,learner='linear',penalty=penalty,gamma=None))
        for penalty,gamma in itertools.product([.02,.2],[.5,2.]):items.append(dict(name=mask['name']+'-'+view+'-rbf-'+str(penalty)+'-'+str(gamma),mask=mask,view=view,learner='rbf',penalty=penalty,gamma=gamma))
    return items

def fit_readout(x,y,ids,recipe,device,out,check):
    from .governance import authorize_training
    authorize_training()
    if recipe['learner']=='lda':
        from .shrinkage_readout import fit_closed_form
        check();result={}
        for prefix,take,label in [('active__',np.ones(len(y),bool),(y>0).astype('int64')),('direction__',y>0,y[y>0]-1)]:
            fitted=fit_closed_form(x[take],label,ids[take],recipe['shrinkage'],Path(str(out)+'-'+prefix));result.update({prefix+k:v for k,v in fitted.items()})
        return result
    a=fit_kernel(x,y,ids,recipe['gamma']) if recipe['learner']=='rbf' else {};values=kernel_features(x,a) if a else x;head=fit_architecture(values,y,ids,recipe.get('architecture','hierarchical'),recipe['penalty'],device,out,check);return dict(a,**head)

def make_protocol(ds,root,sources,prior):
    return dict(schema=SCHEMA,dataset_sha256=digest(ds),code_sha256={p.relative_to(root).as_posix():file_hash(p) for p in sorted((Path(root)/'gnautilus_ml').glob('*.py'))},sources=sources,candidates=candidates(),steps_per_head=250,seed=17,scope='same_wearing_exploratory_trial_grouped_readout_adaptation',independent_test=False,folds=['train','validation','test'],label_rule='same frozen strict navigation intervals; UNKNOWN and official REST excluded',eye_weights_and_policy='source_v3_unchanged',navigation_policy='source_raw_argmax_unchanged',calibration='held-role temperature NLL, exploratory',selection='raw macro >= baseline + .01; no added software REST starts; wrong accepted macro <= baseline + .01; maximize accepted macro minus half wrong macro',prior_elapsed_s=float(prior),max_wall_seconds=3600,total_budget_seconds=43200)

def check_protocol(p,ds,root):
    from .ocular_event_data import compile_annotations
    from .personal_study import verify_sources
    if ds.get('schema')!='gnautilus.full_personal_navigation.v1' or ds['events']!=compile_annotations(ds['catalog'],ds['export']):raise ValueError('Frozen source labels changed')
    if p!=make_protocol(ds,Path(root),p['sources'],p['prior_elapsed_s']) or p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>43200:raise ValueError('Recipe/source/budget changed')
    base=Path(ds['parent']['folder']).parent.parent
    for s in p['sources'].values():
        if file_hash(within(base/s['path'],base))!=s['sha256']:raise ValueError('Frozen spectral input changed')
    receipt=read_json(base/p['sources']['cache_receipt']['path'])
    if receipt['dataset_sha256']!=digest(ds) or receipt['cache_sha256']!=p['sources']['features']['sha256'] or receipt['frames_sha256']!=p['sources']['frames']['sha256'] or receipt['max_original_spectral_difference']>1e-5 or receipt['max_original_latent_difference']>1e-5:raise ValueError('Cache provenance differs')
    if receipt['feature_code_sha256']!=file_hash(Path(root)/'gnautilus_ml/spectral_views.py'):raise ValueError('Runtime feature transform differs from extraction')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt']);return True

def run(base,root,p,ds,profile,out):
    import torch
    from .governance import training_authority,compute_device
    from .artifacts import load_artifact,save_artifact
    start=time.monotonic();out=Path(out);updates=0
    select=select_view;Model=SpectralHybridModel;runtime=RUNTIME;fine=p['schema']=='gnautilus.fine_spectrum_study.v1';covariance=p['schema']=='gnautilus.covariance_spectrum_study.v1'
    if fine:
        from .fine_spectrum import select_view as select
        from .fine_navigation import FineHybridModel as Model,RUNTIME as runtime
    if covariance:
        from .covariance_spectrum import select_view as select
        from .covariance_navigation import CovarianceHybridModel as Model,RUNTIME as runtime
    def check():
        if time.monotonic()-start>p['max_wall_seconds']:raise InterruptedError('Spectral study budget exhausted')
    with training_authority(p,ds,root,profile,out/'protocol.json'):
        device=compute_device();torch.set_num_threads(2);source=Path(base)/p['sources']['manifest']['path'];model,meta=load_artifact(source.parent,device=device);frames=read_json(Path(base)/p['sources']['frames']['path']);valid=[f for f in frames if f['cache_index']>=0];ids=np.array([f['trial_id'] for f in valid]);roles=np.array([f['split'] for f in valid]);y=np.array([f['navigation_label'] if not f['warming'] else -1 for f in valid]);take=y>=0
        with np.load(Path(base)/p['sources']['features']['path'],allow_pickle=False) as z:
            full=z['full'].copy();latent=z['latent'].copy();history_samples=z['samples'].copy() if covariance else None;source_warming=[f['warming'] for f in valid]
            if fine:
                for f in valid:f['warming']=bool(f['warming'] or not z['ready'][f['cache_index']])
                y=np.array([f['navigation_label'] if not f['warming'] else -1 for f in valid]);take=y>=0
                atomic(out/'effective-frames.json',frames)
        with np.load(Path(base)/p['sources']['predictions']['path'],allow_pickle=False) as z:cache={k:z[k].copy() for k in z.files}
        baseline_raw=scores_report(y[take],cache['navigation_oof'][take],ids[take]);baseline_nav,_=replay(frames,cache['navigation_oof'],cache['eye_oof'],cache['motion_oof'],model.details,'raw_argmax');items=[];predictions={};features={}
        for recipe in p['candidates']:
            check();architecture=recipe.get('architecture','hierarchical');key=(recipe['mask']['name'],recipe['view'])
            if covariance:
                for f,w in zip(valid,source_warming):f['warming']=bool(w or history_samples[f['cache_index']]<int(recipe['view'].rsplit('_',1)[1]))
                y=np.array([f['navigation_label'] if not f['warming'] else -1 for f in valid]);take=y>=0
            if key not in features:features[key]=np.concatenate((select(full,recipe['mask']['indices'],recipe['view']),latent),1)
            x=features[key];prob=np.zeros((len(x),5));folder=out/recipe['name'];folder.mkdir(exist_ok=True)
            for role in p['folds']:
                train=(roles!=role)&take;held=roles==role;arrays=fit_readout(x[train],y[train],ids[train],recipe,device,folder/role,check);updates+=(1 if architecture=='flat' else 2)*p['steps_per_head'];prob[held]=evidence(x[held],arrays,'',architecture,[1.] if architecture=='flat' else [1.,1.])[0]
            temps=calibrate(prob[take],y[take],ids[take],architecture);prob=rescale(prob,architecture,temps);raw=scores_report(y[take],prob[take],ids[take]);nav,_=replay(frames,prob,cache['eye_oof'],cache['motion_oof'],model.details,'raw_argmax');item=dict(recipe=recipe,temperatures=temps,classification=raw,navigation=nav,features=x.shape[1]);items.append(item);predictions[recipe['name']]=prob;atomic(out/'candidates.json',items);atomic(out/'progress.json',dict(candidate=recipe['name'],completed=len(items),total=len(p['candidates']),actual_updates=updates,elapsed_s=time.monotonic()-start));print(recipe['name'],'raw',raw['balanced_accuracy'],'accepted',nav['balanced_accuracy'],'REST starts',nav['false_starts_on_software_REST'],flush=True)
        eligible=[v for v in items if v['classification']['balanced_accuracy']>=baseline_raw['balanced_accuracy']+.01 and v['navigation']['false_starts_on_software_REST']<=baseline_nav['false_starts_on_software_REST'] and v['navigation']['wrong_accepted_macro']<=baseline_nav['wrong_accepted_macro']+.01]
        selected=max(eligible,key=lambda q:(q['navigation']['balanced_accuracy']-.5*q['navigation']['wrong_accepted_macro'],q['classification']['balanced_accuracy'])) if eligible else max(items,key=lambda q:q['classification']['balanced_accuracy']);freeze=dict(selected=selected,admitted=bool(eligible),baseline_classification=baseline_raw,baseline_navigation=baseline_nav,independent_test=False);atomic(out/'selection-freeze.json',freeze);recipe=selected['recipe'];architecture=recipe.get('architecture','hierarchical')
        if covariance:
            for f,w in zip(valid,source_warming):f['warming']=bool(w or history_samples[f['cache_index']]<int(recipe['view'].rsplit('_',1)[1]))
            y=np.array([f['navigation_label'] if not f['warming'] else -1 for f in valid]);take=y>=0;atomic(out/'effective-frames.json',frames)
        values=features[(recipe['mask']['name'],recipe['view'])];a=fit_readout(values[take],y[take],ids[take],recipe,device,out/'final',check);updates+=(1 if architecture=='flat' else 2)*p['steps_per_head']
        arrays={k:v.copy() for k,v in model.arrays.items() if not k.startswith('nav__')};arrays.update({'nav__'+k:v for k,v in a.items()});mask=np.zeros(32,dtype='float32');mask[recipe['mask']['indices']]=1;arrays['navigation_input_weights']=mask;details=dict(model.details,feature_mode='absolute',spectral_view_mode=recipe['view'],spectral_view_schema='gnautilus.causal_spectral_view.v1',selected_channels=[CHANNELS[i] for i in recipe['mask']['indices']],navigation_architecture=architecture,navigation_features='joint',navigation_temperatures=selected['temperatures'],new_training=('shrinkage_LDA_on_frozen_JEPA' if recipe['learner']=='lda' else 'regularized_spectral_view_and_optional_RBF_readout_on_frozen_JEPA'),readout_recipe=recipe);details.update(spectral_view_schema='gnautilus.causal_log_spd.v1' if covariance else ('gnautilus.causal_fine_spectrum.v1' if fine else 'gnautilus.causal_spectral_view.v1'),minimum_history_samples=int(recipe['view'].rsplit('_',1)[1]) if covariance else (500 if fine else 375));new=Model(arrays,details,device);prob=evidence(values,arrays,'nav__',architecture,selected['temperatures'])[0];actual,rows=replay(frames,prob,cache['eye'],cache['motion_final'],details,'raw_argmax');fit=scores_report(y[take],prob[take],ids[take]);evidence_meta={k:v for k,v in meta.items() if k not in ['model_id','schema','runtime_schema','details','weights_sha256']};evidence_meta.update(runtime_schema=runtime,source_model_id=meta['model_id'],readout_protocol_sha256=digest(p),readout_code_sha256=p['code_sha256'],readout_gradient_updates=updates,runtime_accepted=False,independent_test=False);evidence_meta['metrics']=dict(training=fit,development=selected['classification'],continuous_replay=actual);path=save_artifact(out/'artifacts',new,evidence_meta);_,confirmed=load_artifact(path,device=device)
        result=dict(schema=p['schema'],source_model=meta['model_id'],model_id=confirmed['model_id'],artifact_path=str(path),weights_sha256=confirmed['weights_sha256'],selected=selected,admitted=bool(eligible),baseline_classification=baseline_raw,baseline_navigation=baseline_nav,final_classification=fit,actual_navigation=actual,actual_gradient_updates=updates,closed_form_fits=2*(3*len(p['candidates'])+1) if all(c['learner']=='lda' for c in p['candidates']) else 0,elapsed_s=time.monotonic()-start,independent_test=False,real_hardware_acceptance=False)
        np.savez(out/'predictions.npz',navigation=prob,navigation_oof=predictions[recipe['name']],eye=cache['eye'],eye_oof=cache['eye_oof'],motion_final=cache['motion_final'],motion_oof=cache['motion_oof']);atomic(out/'replay-frames.json',rows);atomic(out/'report.json',result);print({k:v for k,v in result.items() if k not in ['selected','baseline_classification','baseline_navigation','final_classification','actual_navigation']});return result

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for name in ['root','code-root','protocol','dataset','profile','output']:ap.add_argument('--'+name,required=True)
    a=ap.parse_args()
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve() or Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Checked protocol/output required')
    run(Path(a.root),Path(a.code_root),read_json(a.protocol),read_json(a.dataset),read_json(a.profile),Path(a.output))

if __name__=='__main__':main()
