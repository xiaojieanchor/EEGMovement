"""Read-only source audit. Writes evidence only beneath docs/gnautilus/evidence."""
from __future__ import annotations
import collections, csv, hashlib, io, json, re, zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent

def sha(data):
    return hashlib.sha256(data).hexdigest()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def category(path):
    s = str(path).lower()
    for key, value in [('classification','在线分类与决策'), ('featureextraction','特征提取'),
        ('mindgame','P300与VR'), ('preprocessingblock','P300与VR'), ('avatarcontrol','运动反馈与Avatar'),
        ('emgeog','EMG/EOG'), ('motorimagerybandpower','运动想象频带功率'),
        ('fnirs','EEG/fNIRS辅助'), ('nautilus','g.Nautilus设备工具'),
        ('externalparadigm','外部范式'), ('datastream','记录与回放'), ('dataacquisition','采集与数字输入'),
        ('estim','电刺激辅助'), ('eyetrack','眼动仪辅助'), ('tobii','眼动仪辅助'),
        ('eyelink','眼动仪辅助'), ('stimbox','刺激标记辅助'), ('cube','多模态辅助'),
        ('audiovideo','音视频辅助')]:
        if key in s:return value
    return '通用库或示例'

def parse_slx(path, code):
    data=path.read_bytes(); z=zipfile.ZipFile(io.BytesIO(data)); inner=None
    if path.suffix.lower()=='.sltx':
        inner=next(n for n in z.namelist() if n.endswith('.slx'))
        inner_bytes=z.read(inner);z=zipfile.ZipFile(io.BytesIO(inner_bytes))
    else:inner_bytes=data
    target=OUT/'models'/code;target.mkdir(parents=True,exist_ok=True)
    model={'id':code,'path':path.relative_to(ROOT).as_posix(),'sha256':sha(data),
        'container_model':inner,'model_sha256':sha(inner_bytes),'category':category(path),
        'evidence_level':'模型结构可查；未运行','blocks':[],'lines':[],
        'library_links':[],'sfunctions':[],'callbacks':[],'xml_members':[],
        'top_level_block_count':0}
    for name in z.namelist():
        if not name.endswith('.xml'):continue
        raw=z.read(name)
        try:tree=ET.fromstring(raw)
        except ET.ParseError:continue
        structural = name == 'simulink/blockdiagram.xml' or name.startswith('simulink/systems/')
        blocks=tree.findall('.//Block') if structural else []
        if blocks or 'blockdiagram' in name or 'configSet' in name or 'metadata/' in name:
            dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
            model['xml_members'].append(name)
        for b in blocks:
            ps={p.get('Name',''):''.join(p.itertext()) for p in b.findall('P')}
            masks=[ET.tostring(m,encoding='unicode') for m in b.findall('Mask')]
            item={'member':name,'sid':b.get('SID'),'name':b.get('Name'),
                  'type':b.get('BlockType'),'properties':ps,'masks':masks}
            model['blocks'].append(item)
            if name.endswith('system_root.xml'):model['top_level_block_count']+=1
            if ps.get('SourceBlock'):model['library_links'].append(ps['SourceBlock'])
            if ps.get('FunctionName'):model['sfunctions'].append(ps['FunctionName'])
        for line in (tree.findall('.//Line') if structural else []):
            props={p.get('Name',''):''.join(p.itertext()) for p in line.findall('P')}
            dsts=[p.text for p in line.findall('.//P') if p.get('Name')=='Dst']
            model['lines'].append({'member':name,'source':props.get('Src'),
                                   'destinations':dsts,'properties':props})
        for p in tree.findall('.//P'):
            nm=p.get('Name','')
            if re.search(r'(Fcn$|Callback|MaskInitialization|MaskDisplay)',nm):
                value=''.join(p.itertext())
                if value.strip():model['callbacks'].append({'member':name,'name':nm,'text':value})
    model['library_links']=sorted(set(model['library_links']))
    model['sfunctions']=sorted(set(model['sfunctions']))
    write_json(target/'structure.json',model)
    return model

def parse_mdl(text):
    """Parse line-oriented MDL objects, including branches; never evaluate values."""
    root={'kind':'ROOT','properties':{},'children':[]}; stack=[root]; last_key=None
    for number,line in enumerate(text.splitlines(),1):
        line=line.strip()
        if not line:continue
        opening=re.match(r'^(\w+)\s*\{\s*$',line)
        if opening:
            node={'kind':opening.group(1),'properties':{},'children':[],'line':number}
            stack[-1]['children'].append(node);stack.append(node);last_key=None
        elif line=='}':
            if len(stack)>1:stack.pop()
            last_key=None
        else:
            prop=re.match(r'^(\w+)\s+(.+)$',line)
            if prop:
                last_key=prop.group(1);stack[-1]['properties'][last_key]=prop.group(2).strip('"')
            elif line.startswith('"') and last_key:
                stack[-1]['properties'][last_key]+=line.strip('"')
    blocks=[];lines=[]
    def visit(node,system):
        props=node['properties'];kind=node['kind']
        if kind=='System':system=system+'/'+props.get('Name','System')
        if kind=='Block':
            blocks.append({'member':'PreprocessingBlock.mdl','sid':None,'name':props.get('Name'),
                'type':props.get('BlockType'),'properties':props,'masks':[],
                'system':system,'source_line':node.get('line')})
        if kind=='Line':
            dest=[]
            def ends(n):
                if n['properties'].get('DstBlock'):dest.append(n['properties']['DstBlock']+':'+n['properties'].get('DstPort',''))
                for c in n['children']:ends(c)
            ends(node)
            lines.append({'member':'PreprocessingBlock.mdl','system':system,
                'source':props.get('SrcBlock','')+':'+props.get('SrcPort',''),
                'destinations':dest,'properties':props,'source_line':node.get('line')})
        for child in node['children']:visit(child,system)
    visit(root,'')
    return blocks,lines

def main():
    supplied=[]
    for folder in ['g-tec','programfile_gtec']:
        supplied.extend(p for p in (ROOT/folder).rglob('*') if p.is_file())
    supplied.append(ROOT/'中文版湿电极g.Nautilus使用说明 - 01.pdf')
    def item(p):
        return {'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,
                'suffix':p.suffix.lower(),'sha256':sha(p.read_bytes())}
    with ThreadPoolExecutor(max_workers=4) as pool:inventory=list(pool.map(item,sorted(supplied)))
    prior=json.loads((ROOT/'devices/gnautilus/supplied-materials.json').read_text('utf-8'))
    old={f['path']:f for f in prior['files']};current={f['path']:f for f in inventory}
    changed=[k for k in current.keys()&old.keys() if current[k]['sha256']!=old[k]['sha256']]
    identical=collections.defaultdict(list)
    for f in inventory:identical[f['sha256']].append(f['path'])
    duplicates=[{'sha256':k,'paths':v} for k,v in identical.items() if len(v)>1]
    write_json(OUT/'source_inventory.json',{'files':inventory,'file_count':len(inventory),
        'bytes':sum(f['bytes'] for f in inventory),'suffix_counts':dict(collections.Counter(f['suffix'] for f in inventory)),
        'prior_registry_comparison':{'changed':changed,'missing':sorted(old.keys()-current.keys()),
                                     'added':sorted(current.keys()-old.keys())},
        'duplicate_content_groups':duplicates})
    basenames=collections.defaultdict(list)
    for f in inventory:basenames[Path(f['path']).stem.lower()].append(f['path'])
    models=[]
    paths=sorted(p for p in supplied if p.suffix.lower() in ['.slx','.sltx','.mdl'])
    for index,p in enumerate(paths,1):
        code=f'M{index:03d}'
        if p.suffix.lower()=='.mdl':
            text=p.read_text('utf-8',errors='replace')
            blocks,lines=parse_mdl(text)
            model={'id':code,'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p.read_bytes()),
                'model_sha256':sha(p.read_bytes()),'category':category(p),'evidence_level':'MDL文本可查；未运行',
                'blocks':blocks,'lines':lines,
                'library_links':re.findall(r'SourceBlock\s+"([^"]+)"',text),
                'sfunctions':re.findall(r'FunctionName\s+"([^"]+)"',text),'callbacks':[],
                'mdl_line_count':len(text.splitlines())}
            write_json(OUT/'models'/code/'structure.json',model)
        else:model=parse_slx(p,code)
        for fn in model['sfunctions']:
            model.setdefault('dependency_candidates',{})[fn]=basenames.get(fn.lower(),[])
        write_json(OUT/'models'/code/'structure.json',model)
        models.append(model)
    summary=[{k:m[k] for k in ['id','path','sha256','model_sha256','category','evidence_level']}|
        {'block_count':len(m['blocks']),'line_count':len(m['lines']),
         'library_links':m['library_links'],'sfunctions':m['sfunctions'],
         'callback_count':len(m['callbacks'])} for m in models]
    write_json(OUT/'model_index.json',summary)
    with (OUT/'model_index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        fields=['id','path','category','evidence_level','block_count','line_count','callback_count','sha256']
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(summary)
    for stem,pn in [('ghisys','programfile_gtec/gtec/gHIsys/Help/gHIsys-User-Manual.pdf'),
                    ('chinese','中文版湿电极g.Nautilus使用说明 - 01.pdf'),
                    ('gbsanalyze','programfile_gtec/gtec/gBSanalyze/Help/gBSanalyze.pdf')]:
        p=ROOT/pn
        cache=OUT/'manuals'/f'{stem}_pages.json'
        if cache.exists() and json.loads(cache.read_text('utf-8'))['sha256']==sha(p.read_bytes()):
            continue
        r=PdfReader(p);pages=[]
        for i,page in enumerate(r.pages):
            content=page.extract_text() or ''
            pages.append({'pdf_page':i+1,'text':content})
        write_json(OUT/'manuals'/f'{stem}_pages.json',{'path':pn,'sha256':sha(p.read_bytes()),
                   'page_count':len(pages),'pages':pages})
    # Immutable baseline for retained runtime; ignore build/cache output and secrets.
    tracked=[]
    for directory in ['hub','configs','protocols','receiver','training','scripts','unity']:
        for p in (ROOT/directory).rglob('*'):
            if p.is_file() and p.suffix.lower() in ['.py','.cs','.json','.md','.cmd']:
                if any(x in p.parts for x in ['Library','Temp','obj','bin','__pycache__','node_modules']):continue
                if 'pair' in p.name.lower() or 'secret' in p.name.lower():continue
                tracked.append({'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p.read_bytes())})
    baseline=OUT/'runtime_baseline.json'
    if not baseline.exists():write_json(baseline,tracked)
    print(json.dumps({'files':len(inventory),'models':len(models),
        'slx_sltx_blocks':sum(len(m['blocks']) for m in models if not m['path'].endswith('.mdl')),
        'mdl_blocks':sum(len(m['blocks']) for m in models if m['path'].endswith('.mdl')),
        'source_registry_changed':changed,'duplicate_groups':len(duplicates),
        'runtime_baseline_files':len(tracked)},ensure_ascii=False))

if __name__=='__main__':main()
