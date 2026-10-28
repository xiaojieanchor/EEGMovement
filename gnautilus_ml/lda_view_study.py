"""Locked covariance-aware follow-up to the linear/RBF spectral study."""
import itertools
from pathlib import Path
from .spectral_view_study import make_protocol as spectral_protocol,run
from .common import digest,file_hash,within,read_json

SCHEMA='gnautilus.lda_view_study.v1'

def make_protocol(ds,root,sources,prior):
    p=spectral_protocol(ds,root,sources,prior);masks=[]
    for c in p['candidates']:
        if c['mask'] not in masks:masks.append(c['mask'])
    p.update(schema=SCHEMA,steps_per_head=0,fitting='closed_form_class_trial_balanced_shrinkage_LDA',candidates=[dict(name=mask['name']+'-'+view+'-lda-'+str(shrink),mask=mask,view=view,learner='lda',shrinkage=shrink,gamma=None,penalty=None) for mask,view,shrink in itertools.product(masks,['absolute','band_ratio','temporal_contrast','combined'],[.1,.5,.9])]);return p

def check_protocol(p,ds,root):
    from .ocular_event_data import compile_annotations
    from .personal_study import verify_sources
    if ds.get('schema')!='gnautilus.full_personal_navigation.v1' or ds['events']!=compile_annotations(ds['catalog'],ds['export']):raise ValueError('Frozen source labels changed')
    if p!=make_protocol(ds,Path(root),p['sources'],p['prior_elapsed_s']) or p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>43200:raise ValueError('Locked LDA recipe/source/budget differs')
    base=Path(ds['parent']['folder']).parent.parent
    for s in p['sources'].values():
        if file_hash(within(base/s['path'],base))!=s['sha256']:raise ValueError('Frozen covariance input changed')
    receipt=read_json(base/p['sources']['cache_receipt']['path'])
    if receipt['dataset_sha256']!=digest(ds) or receipt['cache_sha256']!=p['sources']['features']['sha256'] or receipt['feature_code_sha256']!=file_hash(Path(root)/'gnautilus_ml/spectral_views.py'):raise ValueError('Causal feature cache differs')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt']);return True

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for name in ['root','code-root','protocol','dataset','profile','output']:ap.add_argument('--'+name,required=True)
    a=ap.parse_args()
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve() or Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Checked protocol/output required')
    run(Path(a.root),Path(a.code_root),read_json(a.protocol),read_json(a.dataset),read_json(a.profile),Path(a.output))

if __name__=='__main__':main()
