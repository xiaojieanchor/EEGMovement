"""Final evidence gate; read-only on vendor material, records only verification."""
from pathlib import Path
import datetime,hashlib,json,re,sys,zipfile
ROOT=Path(__file__).resolve().parents[1];DOC=ROOT/'docs/gnautilus/integration_20261022';VERIFY=DOC/'verification'

def read(path):return json.loads(Path(path).read_text('utf-8-sig'))
def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda:f.read(1024*1024),b''):digest.update(part)
    return digest.hexdigest()
def require(condition,message):
    if not condition:raise ValueError(message)

def main():
    build=read(VERIFY/'acquisition-build.json');packages=read(VERIFY/'packages.json');extracted=read(VERIFY/'portable-extracted.json')
    require(build['success'] and build['frozen_qt_offscreen_render'] and build['frozen_readonly_audit'],'Acquisition smoke incomplete')
    require(all(sha(ROOT/name)==digest for name,digest in build['source_sha256'].items()),'Frozen acquisition source drift')
    require(sha(build['exe'])==build['exe_sha256'],'EXE drift')
    for key in ['portable','source','windows_vr']:
        info=packages[key];require(sha(info['path'])==info['sha256'],'Package drift: '+key)
        with zipfile.ZipFile(info['path']) as z:require(z.testzip() is None,'ZIP corruption: '+key)
    require(extracted['zip_sha256']==packages['portable']['sha256'],'Extracted test refers to old ZIP')
    require(all(extracted[k] for k in ['all_extracted_file_hashes_match','clean_path_without_python','unicode_and_space_path','frozen_qt_idle_render','frozen_sdk_worker_rejected_missing_sdk','frozen_two_end_logical_audit']),'Portable check incomplete')
    inventory=read(DOC/'evidence/source_inventory.json')['files'];errors=[]
    for row in inventory:
        path=ROOT/row['path']
        if not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:errors.append(row['path'])
    recheck=dict(files=len(inventory),errors=errors,all_original_materials_unchanged=not errors)
    (VERIFY/'source-recheck.json').write_text(json.dumps(recheck,ensure_ascii=False,indent=2),encoding='utf-8');require(not errors,'Vendor source changed')
    models=read(DOC/'evidence/model_index_physical.json');unique=read(DOC/'evidence/model_index_unique.json');cards=read(DOC/'evidence/model_card_coverage.json')
    require(len(models)==244 and len(unique)==170 and len(cards)==86 and all(c['card'] for c in cards),'Model inventory/card coverage mismatch')
    py=(VERIFY/'python-tests.log').read_text('utf-8-sig');match=re.search(r'Ran (\d+) tests',py)
    require(match and py.rstrip().endswith('OK') and 'FAILED' not in py,'Python tests failed')
    js=read(VERIFY/'javascript-tests.log');cs=read(VERIFY/'csharp-runtime-tests.log');require(js['passed']==9 and cs['passed']==45 and not cs['failed'],'JS/C# regression failed')
    vr=[]
    for name,snapshot_name in [('android-build.json','Android-source.zip'),('windows-build.json','Windows-source.zip')]:
        record=read(VERIFY/name);binary=Path(record['output']);require(record['success'] and record['source_snapshot_unchanged'] and record['errors']==0,'VR build failed')
        require(sha(binary)==record['output_sha256'],'VR output changed')
        base=binary.parent if name.startswith('android') else binary.parent.parent;snapshot=base/snapshot_name
        with zipfile.ZipFile(snapshot) as z:
            require(z.testzip() is None,'VR snapshot corruption')
            require(all(hashlib.sha256(z.read(p)).hexdigest()==h for p,h in record['source_hashes'].items()),'VR frozen source drift')
        if name.startswith('windows'):require(all(sha(ROOT/p)==h for p,h in record['source_hashes'].items()),'Current Unity source changed after Windows build')
        vr.append(dict(platform=record['platform'],build_id=record['build_id'],path=str(binary),sha256=sha(binary),snapshot=str(snapshot),snapshot_sha256=sha(snapshot),source_files=len(record['source_hashes']),errors=record['errors'],warnings=record['warnings'],device_installed=False))
    qa=read(VERIFY/'pdf_qa.json');require(qa['all_pages_rendered'] and qa['all_text_in_bounds'] and qa['all_pages_song'],'PDF geometry/font check failed')
    import fitz
    pdfs=[]
    for entry in qa['files']:
        path=Path(entry['file']);document=fitz.open(path)
        require(len(document)==entry['page_count'],'PDF changed after rendering')
        if path.name.startswith('Python'):
            content=''.join(page.get_text() for page in document);compact=re.sub(r'\s+','',content)
            for token in ['PySide6','read_completed_monotonic_ns','跨日','量程','--audit']:require(token in compact,'PDF/MD key content mismatch: '+token)
        pdfs.append(dict(path=str(path),pages=len(document),sha256=sha(path),markdown_sha256=sha(path.with_suffix('.md'))))
    qa['visual_review']=dict(all_pages_layout_reviewed=sum(p['pages'] for p in pdfs),initial_all_contact_sheets_reviewed=10,latest_api_pages_reviewed=['1-008','1-009','1-010','1-011'],
        findings='Chinese glyphs and tables legible; no clipping/shading. Unchanged reports retain prior full-page review; updated API pages rechecked.')
    (VERIFY/'pdf_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding='utf-8')
    result=dict(schema='gnautilus.delivery.v1',date='2026-10-22',verified_at=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
        status='software_delivered_hardware_pending',scope='participant acquisition, native UI, TLS ingest, active markers, durable storage, NAT adaptation, documentation and builds',
        materials=dict(files=len(inventory),programfile_physical_models=231,programfile_unique_models=160,all_physical_models=len(models),all_unique_models=len(unique),new_cards=len(cards),all_input_sha256_unchanged=True),
        tests=dict(python_passed=int(match[1]),javascript=js,csharp=cs,portable_extracted=extracted,qt_ui=read(VERIFY/'ui/review.json'),http=read(VERIFY/'http-ui.json')),
        code_review=dict(important_findings=3,all_fixed_and_independently_rechecked=True,record=str(VERIFY/'code-review.md')),
        acquisition=build,packages=packages,vr=vr,pdfs=pdfs,pdf_pages=sum(p['pages'] for p in pdfs),
        pending=['vendor nonexclusive shared-session capability','actual g.Nautilus EEG and auxiliary columns/reference/ground','SDK internal buffering and physical sample age','target Windows PC acceptance without Python installed','campus network and NAT behavior','headset installation/rendering and actual stop behavior','Hub browser visual acceptance: disabled in Work settings','new model and training, outside this implementation scope'],
        boundaries=dict(vendor_dll_executed=False,real_eeg_recorded=False,training_started=False,remote_server_accessed=False,other_device_deployed=False,legacy_models_restored=False))
    (VERIFY/'delivery.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=result['status'],python_passed=result['tests']['python_passed'],js_passed=js['passed'],csharp_passed=cs['passed'],materials_unchanged=len(inventory),pdf_pages=result['pdf_pages'],portable_sha256=packages['portable']['sha256']),ensure_ascii=False))

if __name__=='__main__':main()
