"""Read-only analysis of real, already committed public-pilot outputs."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import numpy as np


def hash_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for part in iter(lambda:source.read(4*1024**2),b''):h.update(part)
    return h.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def measured(y,p,groups,classes):
    per=[]
    for group in np.unique(groups):
        at=groups==group
        ba=float(np.mean([np.mean(p[at][y[at]==label]==label) for label in np.unique(y[at])]))
        per.append(dict(group=str(group),balanced_accuracy=ba,windows=int(at.sum())))
    rng=np.random.default_rng(17);values=np.array([v['balanced_accuracy'] for v in per])
    bootstrap=np.mean(rng.choice(values,(2000,len(values)),replace=True),axis=1)
    matrix=[[int(np.sum((y==a)&(p==b))) for b in classes] for a in classes]
    return dict(accuracy=float(np.mean(y==p)),group_macro_balanced_accuracy=float(values.mean()),
        descriptive_group_bootstrap_95=np.quantile(bootstrap,[.025,.975]).tolist(),bootstrap_seed=17,bootstrap_draws=2000,
        groups=per,windows=len(y),class_order=classes.tolist(),confusion_matrix=matrix,
        recall={str(c):float(np.mean(p[y==c]==c)) for c in classes},
        uncertainty_scope='Resampling development groups only; excludes seed variability and model-selection bias; not prospective acceptance')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--study',required=True);ap.add_argument('--jobs',required=True);a=ap.parse_args()
    root=Path(a.root);study=Path(a.study);launches=json.loads(Path(a.jobs).read_text());db=sqlite3.connect('file:'+str(root/'state.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    results=[];datasets={};errors=[]
    for launch in launches:
        jid=launch['job_id'];job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone());spec=json.loads(job['spec'])
        dataset=json.loads((root/'datasets'/(spec['dataset_id']+'.json')).read_text());datasets[spec['dataset_id']]=dataset
        entry=dict(reader=launch['reader'],algorithm=launch['algorithm'],job_id=jid,state=job['state'],dataset_id=spec['dataset_id'],protocol_id=spec['protocol_id'])
        events=[dict(row,data=json.loads(row['data'])) for row in db.execute('SELECT * FROM events WHERE job_id=? ORDER BY seq',(jid,))]
        entry['events']=events
        if job['state']=='completed':
            mid=job['model_id'];stored=db.execute('SELECT * FROM models WHERE id=?',(mid,)).fetchone();path=Path(stored['path']);meta=json.loads((path/'manifest.json').read_text());canonical=dict(meta);canonical.pop('model_id')
            assert identity(canonical)==mid and hash_file(path/'weights.npz')==meta['weights_sha256']
            assert meta['evaluation_stage']=='public_method_validation' and not meta['runtime_accepted']
            entry.update(model_id=mid,manifest=meta,verified_file_hashes={'weights':hash_file(path/'weights.npz'),'manifest':hash_file(path/'manifest.json')})
            classes=np.asarray(meta['class_labels']);checks={}
            for role in ['training','development']:
                pred_path=root/'logs'/(jid+'.'+role+'.predictions.npz')
                with np.load(pred_path,allow_pickle=False) as z:
                    y=z['targets'];p=z['labels'];prob=z['probabilities'];groups=z['groups']
                assert np.isfinite(prob).all() and np.allclose(prob.sum(1),1,atol=1e-6)
                np.testing.assert_array_equal(p,classes[prob.argmax(1)])
                metrics=measured(y,p,groups,classes)
                assert abs(metrics['group_macro_balanced_accuracy']-meta['metrics'][role]['balanced_accuracy'])<1e-10
                assert metrics['confusion_matrix']==meta['metrics'][role]['confusion_matrix']
                checks[role]=metrics
            entry['recomputed_metrics']=checks
            entry['history']=[v['data'] for v in events if v['message']=='training_progress']
            entry['export_verification']=[v['data'] for v in events if v['message']=='export_verified']
            assert len(entry['export_verification'])==1 and entry['export_verification'][0]['labels_identical']
        elif job['state']=='failed':errors.append(dict(job_id=jid,events=[v for v in events if v['message']=='task_failed']))
        results.append(entry)
    quality=json.loads((study/'quality-progress.json').read_text());quality_summary={}
    for reader,records in quality.get('dataset_records',{}).items():
        role_counts=Counter();role_classes=defaultdict(Counter);groups=defaultdict(set);reasons=Counter();offered=Counter();retained_all=Counter();reports=[]
        for relative,row in quality['records'].items():
            native={'halt':'kaya2018_halt','bci_iv_2a':'bci_iv_2a','eegmmidb':'eegmmidb','openbmi':'openbmi_mi'}[reader]
            if row['dataset']!=native or not row.get('qc_report'):continue
            report=json.loads(Path(row['qc_report']).read_text());offered.update(report['source_class_counts']);retained_all.update(report['retained_class_counts'])
            for trial in report['trials']:
                if not trial['eligible']:reasons.update(trial['reasons'])
            reports.append(dict(path=row['qc_report'],sha256=hash_file(row['qc_report']),coverage=report['coverage'],admitted=report['admitted_for_pilot']))
        actual_hashes={role:set() for role in ['train','development']};duplicates=[]
        for record in records:
            report=json.loads(Path(record['prepared_windows']['report_path']).read_text());role=record['role'];role_counts[role]+=len(report['retained_windows']);role_classes[role].update(report['retained_class_counts'])
            g=record['public_metadata']['grouping'];groups[role].add((record['subject'],g['level'],g['native_id']))
            # Exact window-content overlap catches copied numerical EEG even if
            # the surrounding original headers/source hashes differ.
            with np.load(record['prepared_windows']['path'],allow_pickle=False) as cache:
                for x in cache['x']:actual_hashes[role].add(hashlib.sha256(x.tobytes()).hexdigest())
        overlap=actual_hashes['train']&actual_hashes['development']
        assert not overlap,'Exact numerical window overlap across public roles'
        quality_summary[reader]=dict(admitted_recordings=len(records),roles=dict(role_counts),role_class_support={k:dict(v) for k,v in role_classes.items()},
            groups={k:len(v) for k,v in groups.items()},candidate_trial_classes=dict(offered),gross_retained_trial_classes=dict(retained_all),
            source_trial_count=sum(offered.values()),fitting_window_count=sum(role_counts.values()),
            fitting_coverage=sum(role_counts.values())/sum(offered.values()),rejection_reasons=dict(reasons),
            rejection_reason_counts_overlap=True,exact_numerical_window_overlap_between_roles=len(overlap),qc_reports=reports)
    gross_summary={}
    for native in sorted({v['dataset'] for v in quality['records'].values()}):
        rows=[v for v in quality['records'].values() if v['dataset']==native]
        observations=[];rates=Counter();excluded=Counter();numerical_sources=0
        for row in rows:
            parts=list(row.get('partitions',{}).values()) or [row]
            had_numeric=False
            for part in parts:
                if 'gross_observations' not in part:continue
                observations.append(part['gross_observations']);had_numeric=True
                rate=part.get('source_sampling_rate')
                if rate is None and row.get('qc_report'):
                    rate=json.loads(Path(row['qc_report']).read_text()).get('source_sampling_rate')
                if rate is not None:rates[str(float(rate))]+=1
            numerical_sources+=int(had_numeric)
            if not row.get('admitted'):excluded[row.get('reason') or row.get('scope') or 'insufficient_retained_class_support']+=1
        minima=[v for o in observations for v in o['min_uv'] if v is not None]
        maxima=[v for o in observations for v in o['max_uv'] if v is not None]
        gross_summary[native]=dict(source_records=len(rows),numeric_source_records=numerical_sources,numeric_partitions=len(observations),
            nonfinite_values=sum(sum(o['nonfinite_by_channel']) for o in observations),
            raw_observed_min_uv=min(minima) if minima else None,raw_observed_max_uv=max(maxima) if maxima else None,
            partitions_by_header_rate=dict(rates),outside_initial_fit_reasons=dict(excluded),
            interpretation='Full source extrema include non-task segments, run boundaries and artifacts; they do not describe the retained fitting windows')
    completion=study/'quality-completion.json'
    output=dict(schema='gnautilus.public_pilot_results.v1',updated_utc=datetime.now(timezone.utc).isoformat(),
        quality_complete=json.loads(completion.read_text()) if completion.exists() else None,quality_assessed_files=quality['assessed_files'],
        quality_source_counts=dict(Counter(v['dataset'] for v in quality['records'].values())),quality_errors=quality['errors'],
        quality=quality_summary,gross_source_summary=gross_summary,results=results,failed_jobs=errors,datasets=datasets,
        model_selection='Neural best development group macro BA, earliest tie; fixed classical recipes',
        independent_personal_acceptance=False,source_plan_sha256=quality['plan_sha256'])
    (study/'analysis-results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'completed':sum(r['state']=='completed' for r in results),'jobs':len(results),'quality_sources':output['quality_source_counts'],
                      'quality_errors':len(output['quality_errors']),'failures':len(errors),'analysis':str(study/'analysis-results.json')}))


if __name__=='__main__':main()
