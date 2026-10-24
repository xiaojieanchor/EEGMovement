"""Audit supplied additions without importing or executing any vendor code.

Preserves first-report evidence. Emits current, physical and content-unique indices.
"""
from __future__ import annotations
import ast
import collections
import csv
import hashlib
import importlib.util
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import fitz
from scipy.io import loadmat

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=HERE/'evidence'

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def write(name,obj):
    p=OUT/name;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')

def csvwrite(name,rows,fields):
    p=OUT/name;p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)

def slim_mat(value):
    import numpy as np
    if hasattr(value,'_fieldnames'):return {k:slim_mat(getattr(value,k)) for k in value._fieldnames}
    if isinstance(value,np.ndarray):
        if value.size<=512:return value.tolist() if value.dtype.kind not in ['O'] else [slim_mat(x) for x in value.flat]
        return {'shape':list(value.shape),'dtype':str(value.dtype),'summary':'large array retained only in source'}
    if isinstance(value,np.generic):return value.item()
    return value

def main():
    OUT.mkdir(exist_ok=True)
    paths=sorted(p for folder in ['g-tec','programfile_gtec','mannul'] for p in (ROOT/folder).rglob('*') if p.is_file())
    paths.append(ROOT/'中文版湿电极g.Nautilus使用说明 - 01.pdf')
    def record(p):return {'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'suffix':p.suffix.lower(),'sha256':digest(p)}
    with ThreadPoolExecutor(max_workers=4) as pool:files=list(pool.map(record,paths))
    prior=json.loads((HERE.parent/'evidence/source_inventory.json').read_text('utf-8'))['files']
    old={f['path']:f for f in prior};now={f['path']:f for f in files};byhash=collections.defaultdict(list)
    for f in files:byhash[f['sha256']].append(f['path'])
    added=sorted(now.keys()-old.keys());missing=sorted(old.keys()-now.keys())
    changed=[{'path':n,'previous_sha256':old[n]['sha256'],'current_sha256':now[n]['sha256']} for n in sorted(now.keys()&old.keys()) if now[n]['sha256']!=old[n]['sha256']]
    relocation=[{'previous_path':n,'sha256':old[n]['sha256'],'current_identical_paths':byhash.get(old[n]['sha256'],[])} for n in missing]
    duplicates=[{'sha256':h,'paths':ps,'bytes_each':now[ps[0]]['bytes']} for h,ps in byhash.items() if len(ps)>1]
    write('source_inventory.json',{'schema':'gnautilus-materials-revision-v2','as_of':'2026-10-22','files':files,'count':len(files),'bytes':sum(f['bytes'] for f in files),'extension_counts':dict(collections.Counter(f['suffix'] for f in files)),'duplicate_content_groups':duplicates})
    write('material_delta.json',{'baseline':'docs/gnautilus/evidence/source_inventory.json','baseline_count':len(prior),'added':added,'changed':changed,'missing_paths':missing,'relocations':relocation,'missing_content':[x for x in relocation if not x['current_identical_paths']]})
    csvwrite('source_inventory.csv',files,['path','bytes','suffix','sha256'])
    spec=importlib.util.spec_from_file_location('original_static_scanner',HERE.parent/'evidence/scan_materials.py')
    parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser);parser.OUT=OUT;parser.ROOT=ROOT
    oldmodels=json.loads((HERE.parent/'evidence/model_index.json').read_text('utf-8'))
    prior_hash=collections.defaultdict(list)
    for m in oldmodels:prior_hash[m['sha256']].append(m['id'])
    next_id=max(int(m['id'][1:]) for m in oldmodels)+1
    modelgroups=collections.defaultdict(list)
    for f in files:
        if f['suffix'] in ['.slx','.sltx','.mdl']:modelgroups[f['sha256']].append(f)
    stems=collections.defaultdict(list)
    for f in files:stems[Path(f['path']).stem.lower()].append(f)
    unique=[];physical=[]
    for h,group in modelgroups.items():
        ids=prior_hash.get(h,[])
        if ids:code=sorted(ids)[0]
        else:code=f'M{next_id:03d}';next_id+=1
        f=next((x for x in group if any(m['path']==x['path'] for m in oldmodels)),group[0]);p=ROOT/f['path']
        if p.suffix.lower()=='.mdl':
            t=p.read_text('utf-8',errors='replace');blocks,lines=parser.parse_mdl(t)
            model={'id':code,'path':f['path'],'sha256':h,'model_sha256':h,'category':parser.category(p),'evidence_level':'MDL文本可查；未运行','blocks':blocks,'lines':lines,'library_links':re.findall(r'SourceBlock\s+"([^"]+)"',t),'sfunctions':re.findall(r'FunctionName\s+"([^"]+)"',t),'callbacks':[]}
        else:model=parser.parse_slx(p,code)
        # Reference-block saved overrides live in InstanceData, not direct P nodes.
        # Preserve references into opaque bdmxdata rather than inventing classifier fields.
        if p.suffix.lower()!='.mdl':
            for member in model['xml_members']:
                if not (member=='simulink/blockdiagram.xml' or member.startswith('simulink/systems/')):continue
                tree=ET.parse(OUT/'models'/code/member)
                for node in tree.findall('.//Block'):
                    matches=[b for b in model['blocks'] if b['member']==member and b['sid']==node.get('SID') and b['name']==node.get('Name')]
                    for b in matches:
                        b['instance_parameters']={q.get('Name',''):''.join(q.itertext()) for q in node.findall('InstanceData/P') if not q.get('Name','').startswith('RTWMemSec')}
                        b['property_references']={q.get('Name',''):q.get('Ref') for q in node.findall('P') if q.get('Ref')}
        cat=model['category'];s=f['path'].lower()
        if 'motorimagerycsp' in s:cat='运动想象CSP与FES辅助' if 'fes' in s else '运动想象CSP'
        elif 'motorimagerybp' in s:cat='运动想象频带功率'
        elif '/library/io/' in s:cat='采集、通信与存储'
        elif '/library/signalquality/' in s:cat='信号质量'
        elif '/library/preprocessing/' in s:cat='因果预处理与参考'
        elif '/library/transformation/' in s:cat='变换与特征'
        elif '/library/visualization/' in s:cat='可视化与反馈'
        elif 'ssvep' in s:cat='SSVEP'
        elif 'p300' in s:cat='P300与VR'
        model.update(category=cat,paths=[x['path'] for x in group],previous_ids=ids,new_content=not bool(ids))
        model['dependency_candidates']={name:[x['path'] for x in stems.get(name.lower(),[])] for name in model['sfunctions']}
        model['library_candidates']={name:[x['path'] for x in stems.get(name.split('/')[0].lower(),[]) if x['suffix'] in ['.slx','.mdl','.sltx']] for name in model['library_links']}
        parser.write_json(OUT/'models'/code/'structure.json',model)
        row={k:model[k] for k in ['id','path','paths','sha256','model_sha256','category','evidence_level','previous_ids','new_content','library_links','sfunctions']}
        row.update(block_count=len(model['blocks']),line_count=len(model['lines']),callback_count=len(model['callbacks']),analysis_status='全结构静态解析；未运行')
        unique.append(row)
        for x in group:physical.append({'canonical_id':code,'path':x['path'],'sha256':h,'category':cat,'analysis_status':row['analysis_status'],'previous_ids':','.join(ids)})
    unique.sort(key=lambda x:x['id']);physical.sort(key=lambda x:x['path'])
    write('model_index_unique.json',unique);write('model_index_physical.json',physical)
    csvwrite('model_index_unique.csv',unique,['id','path','category','analysis_status','new_content','block_count','line_count','callback_count','sha256'])
    csvwrite('model_index_physical.csv',physical,['canonical_id','path','category','analysis_status','previous_ids','sha256'])
    sources=[]
    for f in files:
        if f['suffix'] not in ['.m','.h','.hpp','.cpp']:continue
        if not any(x in f['path'] for x in ['MotorImageryCSP','gNEEDaccess Client API','MotorImageryBP','EMGEOGControl']):continue
        p=ROOT/f['path'];text=p.read_text('utf-8-sig',errors='replace');lines=text.splitlines()
        row={'path':f['path'],'sha256':f['sha256'],'lines':len(lines),'functions':[],'parameter_lines':[]}
        for i,t in enumerate(lines,1):
            if re.match(r'\s*function\b',t):row['functions'].append({'line':i,'text':t})
            if re.search(r'frequency|bandpass|notch|sampling|counter|validation|scans|exclusive|creator|GetData|StartAcquisition|StopAcquisition|SetConfiguration|window|trigger|artifactDetection|plotOnly4CSPs|Montage|classrun|crossval|variance|findclass',t,re.I):row['parameter_lines'].append({'line':i,'text':t[:1200]})
        sources.append(row)
    whl=next((ROOT/'programfile_gtec/gtec/gtec/gNEEDaccess Client API/Python').glob('*.whl'))
    z=zipfile.ZipFile(whl);py=z.read('pygds.py').decode('utf-8-sig');sp=OUT/'sources/pygds-1.20.1.py.txt';sp.parent.mkdir(exist_ok=True);sp.write_text(py,encoding='utf-8')
    nodes=[{'name':n.name,'line':n.lineno,'end_line':n.end_lineno,'kind':type(n).__name__} for n in ast.walk(ast.parse(py)) if isinstance(n,(ast.ClassDef,ast.FunctionDef))]
    write('python_api_source_index.json',{'wheel':whl.relative_to(ROOT).as_posix(),'wheel_sha256':digest(whl),'module_sha256':hashlib.sha256(z.read('pygds.py')).hexdigest(),'line_count':len(py.splitlines()),'symbols':nodes,'module_text_path':str(sp.relative_to(ROOT))})
    write('source_parameter_index.json',sources)
    manuals=[]
    for p in sorted((ROOT/'mannul').glob('*.pdf')):
        d=fitz.open(p);pages=[]
        for i,page in enumerate(d):
            t=page.get_text();candidates=[l.strip() for l in t.splitlines() if re.fullmatch(r'\d{1,3}',l.strip())]
            pages.append({'pdf_page':i+1,'printed_page_candidates':candidates,'text':t})
        row={'path':p.relative_to(ROOT).as_posix(),'sha256':digest(p),'page_count':len(d),'pages':pages};manuals.append({k:row[k] for k in ['path','sha256','page_count']});write('manuals/'+p.stem+'_pages.json',row)
    mats=[]
    for p in (ROOT/'programfile_gtec/gtec/gtec/gHIsys/Toolboxes/MotorImageryCSP').rglob('*.mat'):
        if 'Montage' not in str(p) and 'classinfo' not in str(p):continue
        try:vals=loadmat(p,squeeze_me=True,struct_as_record=False);mats.append({'path':p.relative_to(ROOT).as_posix(),'sha256':digest(p),'variables':{k:slim_mat(v) for k,v in vals.items() if not k.startswith('__')}})
        except Exception as e:mats.append({'path':p.relative_to(ROOT).as_posix(),'error':type(e).__name__+': '+str(e)})
    write('csp_mat_parameters.json',mats)
    summary={'as_of':'2026-10-22','inventory_files':len(files),'programfile_files':sum(f['path'].startswith('programfile_gtec/') for f in files),'programfile_bytes':sum(f['bytes'] for f in files if f['path'].startswith('programfile_gtec/')),'added_paths':len(added),'changed_content':len(changed),'missing_paths':len(missing),'relocated_content':sum(bool(x['current_identical_paths']) for x in relocation),'missing_content':sum(not bool(x['current_identical_paths']) for x in relocation),'duplicate_groups':len(duplicates),'physical_models':len(physical),'unique_models':len(unique),'new_unique_models':sum(x['new_content'] for x in unique),'model_block_count':sum(x['block_count'] for x in unique),'model_line_count':sum(x['line_count'] for x in unique),'library_reference_count':sum(len(x['library_links']) for x in unique),'category_counts_unique':dict(collections.Counter(x['category'] for x in unique)),'manuals':manuals}
    write('analysis_summary.json',summary)
    registry=ROOT/'devices/gnautilus/supplied-materials-20261022-completed.json';registry.write_text(json.dumps({'status':'STATIC_REVIEWED_NOT_RUNTIME_VERIFIED','evidence':'docs/gnautilus/integration_20261022/evidence/source_inventory.json','summary':summary,'files':files},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':main()
