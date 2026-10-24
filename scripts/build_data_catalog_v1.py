"""Build an ordered, read-only data index; never move data or assign train roles."""
import collections
import csv
import datetime
import hashlib
import io
import json
import os
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def fingerprint(path):
    s=path.stat()
    return s.st_size,s.st_mtime_ns


def record_index(path,live_name,roles):
    stat=fingerprint(path)
    item=dict(recording_id=path.stem,path=path.as_posix(),bytes=stat[0],date=path.stem[:8],
        status='inventory_only',independent_test=False,training_role='unassigned',warnings=[])
    # An unavailable Hub cannot establish that an apparently closed file is stable.
    if live_name is None or path.stem==live_name:
        item.update(status='active_or_unconfirmed_skip',sha256=None)
        return item
    kinds=collections.Counter();epochs=set();donnings=set();placements=set();conditions=set()
    total=0;gaps=0;bad=0;header={};meta={};disabled=set();h=hashlib.sha256()
    with path.open('rb') as stream:
        for number,line in enumerate(stream,1):
            h.update(line)
            try:row=json.loads(line)
            except (ValueError,UnicodeError):
                bad+=1
                continue
            if not isinstance(row,dict):bad+=1;continue
            kind=row.get('kind','unknown');kinds[kind]+=1
            if kind=='session' and not header:header=row
            if kind in ['self_paced_session','guided_session']:
                meta.update({k:row[k] for k in ['input_surface','presentation_protocol','paradigm','control_intent','session_id'] if k in row})
                conditions.add(kind)
            if kind=='calibration':conditions.add('calibration')
            if kind=='model_check':conditions.add('model_check');meta['input_surface']=row.get('surface',meta.get('input_surface','unknown'))
            if row.get('donning_id'):donnings.add(str(row['donning_id']))
            if row.get('placement_id'):placements.add(str(row['placement_id']))
            if isinstance(row.get('disabled_channels_1based'),list):disabled.update(row['disabled_channels_1based'])
            if kind=='eeg':
                samples=row.get('samples_uv',[])
                if isinstance(samples,list):total+=len(samples)
                if isinstance(row.get('source_epoch'),int):epochs.add(row['source_epoch'])
                if row.get('discontinuity') or row.get('gap_packets',0):gaps+=1
    if fingerprint(path)!=stat:
        item.update(status='changed_during_inventory_skip',sha256=None)
        return item
    source_sha=h.hexdigest();assignment=roles.get(path.stem)
    if assignment:
        if assignment['source_sha256']==source_sha:item['training_role']=assignment['role']
        else:item['warnings'].append('canonical_source_hash_mismatch_no_role_assigned')
    if bad:item['warnings'].append('invalid_json_rows_require_review')
    item.update(status='closed_indexed' if not bad else 'invalid_requires_review',sha256=source_sha,
        started_at=header.get('started_at'),source_mode=header.get('source'),sample_rate_hz=header.get('sfreq'),
        unit=header.get('unit'),physical_channels=header.get('channels'),raw_samples=total,
        raw_seconds=total/header['sfreq'] if header.get('sfreq') else None,
        source_epochs=sorted(epochs),discontinuity_packets=gaps,row_kinds=dict(sorted(kinds.items())),
        invalid_rows=bad,collection=meta,conditions=sorted(conditions),
        donning_ids=sorted(donnings),placement_ids=sorted(placements),
        recorded_disabled_channels_1based=sorted(disabled),
        known_inspected=bool('model_check' in conditions or assignment and assignment.get('previously_inspected')),
        qc_duration_computed=False,labels_reinterpreted=False)
    return item


def build(root,live_name,public_root):
    root=Path(root);policy_path=root/'configs/device_channels.json';policy=load(policy_path)
    canonical=root/'configs/causal_personal_records.development.json'
    roles={x['recording']:x for x in load(canonical)['records']} if canonical.exists() else {}
    rows=[record_index(p,live_name,roles) for p in sorted((root/'outputs/sessions').glob('*.jsonl'))]
    groups=collections.defaultdict(list)
    for row in rows:
        if row.get('sha256'):groups[row['sha256']].append(row['recording_id'])
    derived=[]
    metadata=sorted(list((root/'data/personal').glob('**/*.json'))+list((root/'data/prepared').glob('**/*.json')))
    for p in metadata:
        if p.name=='split.json':continue
        value=load(p)
        derived.append(dict(path=p.relative_to(root).as_posix(),metadata_sha256=sha(p),
            source_sha256=value.get('source_sha256'),recording_id=p.parent.name,
            schema=value.get('schema'),samples=value.get('samples'),
            declared_sequence_sha256=value.get('sequence_sha256'),
            declared_disabled_channels_1based=value.get('disabled_channels_1based'),
            large_payload_revalidated=False,role_authority=False))
    splits=[dict(path=p.relative_to(root).as_posix(),sha256=sha(p),role_authority='historical_only_no_reassignment')
        for p in sorted((root/'data/personal').glob('*/split.json'))]
    public=dict(location=str(public_root),state='unavailable',payload_revalidated=False)
    plan_path=Path(public_root)/'download_plan.json';ledger_path=Path(public_root)/'ledger.json'
    if plan_path.exists() and ledger_path.exists():
        plan=load(plan_path);ledger=load(ledger_path);datasets=collections.defaultdict(lambda:dict(files=0,known_bytes=0,unknown_size_files=0,ledger_states=collections.Counter()))
        for entry in sorted(plan.get('entries',[]),key=lambda x:x['id']):
            ds=datasets[entry['dataset_id']];ds['files']+=1;ds['known_bytes']+=int(entry.get('bytes') or 0)
            if not entry.get('bytes'):ds['unknown_size_files']+=1
            ds['ledger_states'][(ledger.get(entry['id']) or {}).get('state','missing')]+=1
        public.update(state='existing_plan_and_ledger_indexed',plan_sha256=sha(plan_path),ledger_sha256=sha(ledger_path),
            datasets=[dict(dataset_id=k,**v) for k,v in sorted(datasets.items())],
            note='Stored ledger states only; no remote payload verification performed by this catalog')
    artifacts=[]
    for name in ['parallel-candidate-summary-latest.json','parallel-comparison-latest.json','completed-new-cohort-v1/latest.json','completed-new-cohort-v1/model-packages.json',
                 'fresh-remediation-v1/collected-latest.json','ba-audit-20261019/acceptance.json',
                 'new-recording-gpu1-v2/source-and-run-index.json','gpu1-policy-v1/acceptance.json',
                 'rest-balance-v1/source-and-run-index.json','new-recording-screen-v1/collected-latest.json',
                 'rest-balance-v1/completed-comparison.json','new-scope-gpu1-v1/source-and-run-index.json',
                 'new-scope-screen-v1/collected-latest.json','new-scope-gpu1-v1/followup-v2.json','early-stop-new-full-v1/stop-receipt.json',
                 'early-stop-new-full-v1/continuation-index.json','recording-distribution-audit-v2/report.json',
                 'stopped-full-evaluation-v1/launch.json','stopped-full-evaluation-v1/latest.json',
                 'stopped-full-evaluation-v1/report.json','source-alignment-audit-v1/launch.json',
                 'source-alignment-audit-v1/report.json','source-aligned-entry-v2/source-aligned-v1-generated-cuda/report.json',
                 'source-aligned-training-v2/source-and-run-index.json','early-stop-public-scope-v1/stop-receipt.json',
                 'baseline-gpu1-entry-v2/baseline-gpu1-v2-generated-cuda/report.json','baseline-gpu1-training-v2/source-and-run-index.json',
                 'baseline-gpu1-training-v2/collected-results.json','baseline-gpu1-training-v2/completed-analysis.json',
                 'reset-head-entry-v1/reset-head-v1-generated-cuda/report.json','reset-head-training-v1/source-and-run-index.json',
                 'reset-head-training-v1/collected-result.json','public-stopped-evaluation-v1/latest.json','public-stopped-evaluation-v1/report.json',
                 'motion-first-p0-v1/metadata-copy.json','motion-first-p0-v1/report.json','motion-first-p0-v1/source-and-run-index.json']:
        p=root/'outputs/refactor_v2'/name
        if p.exists():artifacts.append(dict(path=p.relative_to(root).as_posix(),sha256=sha(p)))
    checks=[]
    for path in sorted((root/'outputs/model_checks').glob('*/*/report.json')):
        try:
            report=load(path);raw=report.get('raw_recording') or {}
            checks.append(dict(check_id=report['check_id'],created_at=report.get('created_at'),mode=report.get('mode'),state=report.get('state'),
                recording_id=report.get('recording'),report_path=path.relative_to(root).as_posix(),report_sha256=sha(path),
                model_id=(report.get('model_binding',{}).get('model') or [None])[0],raw_sha256=raw.get('sha256'),
                data_files=report.get('data_files',{}),role=report.get('role'),previously_inspected=True,independent_test=False))
        except (OSError,ValueError,KeyError):continue
    return dict(schema='eegmovement-data-catalog-v1',generated_at=datetime.datetime.now().astimezone().isoformat(),
        inventory_only=True,training_authority=False,original_files_modified=False,
        current_device_policy=dict(path=policy_path.relative_to(root).as_posix(),sha256=sha(policy_path),**policy),
        recording_order='date, recording timestamp, original id; no inferred donning groups',
        summary=dict(recordings=len(rows),closed_indexed=sum(r['status']=='closed_indexed' for r in rows),
            active_or_unconfirmed=sum(r['status']!='closed_indexed' for r in rows),
            local_raw_bytes=sum(r['bytes'] for r in rows),derived_manifests=len(derived)),
        recordings=rows,duplicate_raw_groups=[dict(sha256=k,recordings=v) for k,v in sorted(groups.items()) if len(v)>1],
        derived_manifests=derived,historical_split_references=splits,public=public,
        artifact_indexes=artifacts,model_checks=checks,
        roots=dict(raw='outputs/sessions',personal_derived='data/personal',canonical='data/prepared',
            experiments='outputs/refactor_v2',model_checks='outputs/model_checks',weights='G:/EEGMovement/Models',public=str(public_root)))


def write(root,catalog):
    folder=Path(root)/'data/catalog';folder.mkdir(parents=True,exist_ok=True)
    body=json.dumps(catalog,ensure_ascii=False,indent=2)+'\n'
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    (folder/('catalog-'+stamp+'.json')).write_text(body,encoding='utf-8')
    temporary=folder/'catalog.next.json';temporary.write_text(body,encoding='utf-8');os.replace(temporary,folder/'catalog.json')
    csv_body=io.StringIO(newline='');columns=['date','recording_id','status','raw_seconds','training_role','known_inspected','sha256','path']
    writer=csv.DictWriter(csv_body,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(catalog['recordings'])
    (folder/'personal_recordings.csv').write_text(csv_body.getvalue(),encoding='utf-8-sig')


if __name__=='__main__':
    try:
        with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5) as response:status=json.load(response)
        live=status['recording'].get('name') if status['recording']['active'] else ''
    except Exception:live=None
    catalog=build(ROOT,live,Path('G:/EEGMovement/PublicData'));write(ROOT,catalog)
    print(json.dumps(catalog['summary'],ensure_ascii=False))
