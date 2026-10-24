"""Audit actual deliverables, download identities and unchanged prior sources."""
from pathlib import Path
import concurrent.futures,hashlib,json,re
from datetime import datetime,timezone
from dataclasses import asdict
import fitz
from markdown_it import MarkdownIt
from reportlab.pdfbase.ttfonts import TTFont

BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[2]

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    raw=json.loads((BASE.parent/'evidence/source_inventory.json').read_text('utf-8'))['files']
    runtime=json.loads((BASE.parent/'evidence/runtime_baseline.json').read_text('utf-8'))
    def check(r):
        p=ROOT/r['path'];return r['path'] if not p.is_file() or sha(p)!=r['sha256'] else None
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        changed_raw=[v for v in pool.map(check,raw) if v]
        changed_runtime=[v for v in pool.map(check,runtime) if v]
    downloads=json.loads((BASE/'official_updates/download_receipts.json').read_text('utf-8'))['receipts']
    ds_downloads=json.loads((BASE/'datasets/download_receipts.json').read_text('utf-8'))
    invalid_downloads=[]
    for r in downloads+ds_downloads:
        path=r.get('saved_path',r.get('path'))
        if not path:continue
        p=Path(path)
        if not p.exists() or sha(p)!=r.get('sha256') or p.stat().st_size!=r.get('bytes'):invalid_downloads.append(path)
    registry=json.loads((BASE/'datasets/admission_registry.json').read_text('utf-8'))
    proposal=json.loads((BASE/'protocol_design/protocol_proposal.json').read_text('utf-8'))
    failures=json.loads((BASE/'protocol_design/failure_matrix.json').read_text('utf-8'))['cases']
    md=BASE/'迭代方案与采集评估手册_20261022.md';pdf=md.with_suffix('.pdf');text=md.read_text('utf-8')
    font=TTFont('SongAudit','C:/Windows/Fonts/simsun.ttc',subfontIndex=0)
    missing=sorted({c for c in text if ord(c)>32 and ord(c) not in font.face.charToGlyph})
    doc=fitz.open(pdf);outside=[];wrongfonts=[];replacement=0
    for i,page in enumerate(doc):
        replacement+=page.get_text().count('\ufffd')
        for block in page.get_text('dict')['blocks']:
            if block.get('type')!=0:continue
            for line in block['lines']:
                for s in line['spans']:
                    a,b,c,d=s['bbox']
                    if b>803 and s['size']<9:continue
                    if a<43 or c>page.rect.width-43 or b<29 or d>799:outside.append([i+1,s['text']])
                    if 'SimSun' not in s['font']:wrongfonts.append([i+1,s['font']])
    code=list((ROOT/'gnautilus_lab').glob('*.py'))
    forbidden=[]
    for f in code:
        t=f.read_text('utf-8')
        if re.search(r'^\s*(?:from|import)\s+(?:hub|training|socket|requests|paramiko|pygds|gpype|matlab)',t,re.M):forbidden.append(f.name)
    layout=proposal['montage']['layouts'];layout_ok=all(len(v['positions'])==32 and len(set(v['positions']))==32 for v in layout.values())
    results=json.loads((BASE/'test_results.json').read_text('utf-8'))
    requirements=[
      {'requirement':'采集点位与布局取舍','evidence':['report section2','protocol_design/protocol_proposal.json'],'result':'32 unique A/B positions; A primary; REF/GND/port unknown explicit'},
      {'requirement':'有效且高置信信号评估设计','evidence':['report section3','gnautilus_lab/assessment.py','tests/test_gnautilus_lab.py'],'result':'independent groups, nested refit, causal source controls, uncertainty; real evidence not yet available'},
      {'requirement':'模型结构与Simulink对应','evidence':['report section4','gnautilus_lab/reference.py'],'result':'two-class CSP/LDA math prepared; real fit disabled; MRCP/multiclass/calibration future stages explicit'},
      {'requirement':'MATLAB中间层选择及完整future链路','evidence':['report sections5/9'],'result':'options and licensing/compatibility limits recorded; actual chain deferred by user'},
      {'requirement':'调试问题与解决回归','evidence':['report section6','protocol_design/failure_matrix.json'],'result':'15 symptom/evidence/repair/regression cases'},
      {'requirement':'官方新版尝试与可用示例','evidence':['official_updates/download_receipts.json','report section7'],'result':'official current Python examples and changelog downloaded; host/toolbox distinction; binary download not obtained'},
      {'requirement':'目标相关公开库与不硬融合','evidence':['datasets/admission_registry.json','datasets/download_receipts.json','report section8'],'result':'6 candidates; 5 held,1 rejected for training; no new EEG payload or fusion'},
      {'requirement':'API前可构建项完成／运行边界','evidence':['gnautilus_lab/README.md','test_results.json','design.md'],'result':'offline contract/features/math/evaluation/decision and metadata CLI; no actual hardware/service chain'}]
    checks={'original_materials_unchanged':not changed_raw,'retained_runtime_unchanged':not changed_runtime,
      'all_download_receipt_hashes_match':not invalid_downloads,'both_32_montages_unique':layout_ok,
      'all_six_datasets_not_training_admitted':len(registry['datasets'])==6 and all(not r['approved'] and not r['pretraining_admission'] and not r['supervised_training_admission'] for r in registry['datasets']),
      'no_new_EEG_payload_claim':registry['downloaded_new_EEG_payloads']==0,
      'proposal_not_runnable_and_API_unknown':proposal['status']=='PROPOSAL_NOT_RUNNABLE' and proposal['device']['api_version'] is None and not proposal['real_training_enabled'],
      'fifteen_failure_cases':len(failures)==15,'new_modules_no_old_or_device_network_imports':not forbidden,
      'synthetic_30_and_total127_passed':results['new_synthetic']['passed']==30 and results['full_regression']['passed']==127 and all(results['code_sha256'].get(p.name)==sha(p) for p in code),
      'pdf_no_missing_glyphs':not missing,'pdf_no_replacement':replacement==0,'pdf_body_Song':not wrongfonts,'pdf_body_within_bounds':not outside,
      'completion_audit_record_present':len(requirements)==8}
    outputs={p.name:{'sha256':sha(p),'bytes':p.stat().st_size} for p in [md,pdf]}
    result={'date':'2026-10-22','verified_at_utc':datetime.now(timezone.utc).isoformat(),'checks':checks,
      'all_checks_pass':all(checks.values()),'requirements_audit':requirements,'source_count':len(raw),'runtime_count':len(runtime),
      'changed_raw':changed_raw,'changed_runtime':changed_runtime,'invalid_downloads':invalid_downloads,
      'pdf_pages':len(doc),'pdf_missing_glyphs':missing,'outside_spans':outside,'outputs':outputs,
      'code_sha256':{p.name:sha(p) for p in code},'test_file_sha256':sha(ROOT/'tests/test_gnautilus_lab.py'),
      'test_scope':'synthetic numerical/contract tests + existing software regression; no EEG accuracy or live hardware acceptance',
      'manual_review':{'requirements':'author checked each evidence path and report sections against the full user objective','PDF':'all10 page contact sheet viewed, layout limits and embedded Song checked; no hardware efficacy inference','independence':'author final review; not a new independent reviewer'},
      'remaining_by_user_instruction':['actual Python API acquisition adapter','real device configuration and QC','governed actual EEG model training/assessment','actual service/VR link and visual latency acceptance']}
    (BASE/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':checks,'all_checks_pass':all(checks.values()),'pdf_pages':len(doc),'outputs':outputs},ensure_ascii=False))
    if not all(checks.values()):raise SystemExit(1)

if __name__=='__main__':main()
