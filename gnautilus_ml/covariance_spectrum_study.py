"""Locked multi-channel log-SPD and flat/factorized readout comparison."""
import itertools
from pathlib import Path
from .spectral_view_study import make_protocol as base_protocol,run
from .common import digest,file_hash,within,read_json
SCHEMA='gnautilus.covariance_spectrum_study.v1'

def make_protocol(ds,root,sources,prior):
    p=base_protocol(ds,root,sources,prior);masks=[]
    for c in p['candidates']:
        if c['mask'] not in masks:masks.append(c['mask'])
    p.update(schema=SCHEMA,covariance_bands_hz=[[8,13],[13,30]],covariance_shrinkage=.05,minimum_history_samples=[500,1000],features='symmetric matrix logarithm; sqrt2 off diagonal; frozen JEPA h32; train-fold-only normalizer',candidates=[dict(name=m['name']+'-'+v+'_'+str(w)+'-'+a+'-'+str(l),mask=m,view=v+'_'+str(w),architecture=a,learner='linear',penalty=l,gamma=None) for m,v,w,a,l in itertools.product(masks,['logcov','logcorr'],[500,1000],['flat','hierarchical'],[.05,.2])]);return p

def check_protocol(p,ds,root):
    from .ocular_event_data import compile_annotations
    from .personal_study import verify_sources
    if ds.get('schema')!='gnautilus.full_personal_navigation.v1' or ds['events']!=compile_annotations(ds['catalog'],ds['export']):raise ValueError('Frozen human intervals changed')
    if p!=make_protocol(ds,Path(root),p['sources'],p['prior_elapsed_s']) or p['prior_elapsed_s']<0 or p['prior_elapsed_s']+p['max_wall_seconds']>43200:raise ValueError('Covariance protocol/source/budget changed')
    base=Path(ds['parent']['folder']).parent.parent
    for s in p['sources'].values():
        if file_hash(within(base/s['path'],base))!=s['sha256']:raise ValueError('Locked SPD source changed')
    r=read_json(base/p['sources']['cache_receipt']['path'])
    if r['schema']!='gnautilus.covariance_cache.v1' or r['dataset_sha256']!=digest(ds) or r['cache_sha256']!=p['sources']['features']['sha256'] or r['frames_sha256']!=p['sources']['frames']['sha256'] or r['max_original_spectral_difference']>1e-5 or r['max_original_latent_difference']>1e-5 or r['feature_code_sha256']!=file_hash(Path(root)/'gnautilus_ml/covariance_spectrum.py'):raise ValueError('SPD cache/runtime/encoder support differs')
    verify_sources(ds['parent']['folder'],ds['parent']['source_receipt']);return True

def main():
    import argparse,os
    ap=argparse.ArgumentParser()
    for name in ['root','code-root','protocol','dataset','profile','output']:ap.add_argument('--'+name,required=True)
    a=ap.parse_args()
    if Path(os.environ.get('EEGMOVEMENT_TRAINING_PROTOCOL','')).resolve()!=Path(a.protocol).resolve() or Path(a.output).resolve()!=Path(a.protocol).parent.resolve():raise ValueError('Checked protocol/output required')
    run(Path(a.root),Path(a.code_root),read_json(a.protocol),read_json(a.dataset),read_json(a.profile),Path(a.output))
if __name__=='__main__':main()
