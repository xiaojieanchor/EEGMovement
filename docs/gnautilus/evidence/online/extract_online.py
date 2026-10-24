from pathlib import Path
import hashlib, io, json, re, zipfile
import xml.etree.ElementTree as E
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
targets = [
'programfile_gtec/gtec/gHIsys/Library/Classification.slx',
'programfile_gtec/gtec/gHIsys/Library/Classification/ApplyClassifierModel.sltx',
'programfile_gtec/gtec/gHIsys/Library/Classification/MajorityModel.sltx',
'programfile_gtec/gtec/gHIsys/Library/FeatureExtraction.slx',
'programfile_gtec/gtec/gHIsys/Library/FeatureExtraction/CCAModel.sltx',
'programfile_gtec/gtec/gHIsys/Library/FeatureExtraction/MinimumEnergyModel.sltx',
'programfile_gtec/gtec/gHIsys/Library/FeatureExtraction/ERD.sltx',
'programfile_gtec/gtec/gHIsys/Library/FeatureExtraction/HjorthModel.sltx',
'programfile_gtec/gtec/gHIsys/Library/EMGEOG.slx',
'programfile_gtec/gtec/gHIsys/gEstimFES/MotorImageryBandpower_FES.sltx',
'programfile_gtec/gtec/gHIsys/gSENSORfNIRS/gSENSORfNIRS_MotorImagery.sltx',
'programfile_gtec/gtec/gHIsys/gSENSORfNIRS/Motor Imagery/AvatarControlLib.slx',
'g-tec/gHIsys/VirtualAndAugmentedReality/P300Mastermind/Simulink/MINDgame_VR.sltx',
'g-tec/gHIsys/VirtualAndAugmentedReality/P300Mastermind/Simulink/MINDgame_Replay.sltx',
'g-tec/gHIsys/Examples/External Paradigms/ExternalParadigmDemo.slx',
]
records=[]
for rel in targets:
 p=ROOT/rel
 with zipfile.ZipFile(p) as outer:
  if p.suffix=='.sltx':
   nested=next(n for n in outer.namelist() if n.endswith('.slx'))
   z=zipfile.ZipFile(io.BytesIO(outer.read(nested)))
  else: nested=None; z=outer
  rec=dict(path=rel,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),nested_model=nested,systems=[],other_xml={})
  for name in z.namelist():
   if not name.endswith('.xml'):continue
   try: t=E.fromstring(z.read(name))
   except E.ParseError:continue
   if 'systems/system' in name or name=='simulink/blockdiagram.xml':
    blocks=[]
    for b in t.findall('.//Block'):
     blocks.append(dict(name=b.attrib.get('Name'),type=b.attrib.get('BlockType'),sid=b.attrib.get('SID'),properties={v.attrib.get('Name'):''.join(v.itertext()) for v in b.findall('P')},mask=E.tostring(b.find('Mask'),encoding='unicode') if b.find('Mask') is not None else None,instance_parameters=[dict(name=v.attrib.get('Name'),value=''.join(v.itertext())) for v in b.findall('InstanceData/P')],subsystems=[s.attrib for s in b.findall('System')]))
    rec['systems'].append(dict(xml=name,blocks=blocks,lines=[E.tostring(v,encoding='unicode') for v in t.findall('.//Line')]))
    if name=='simulink/blockdiagram.xml':rec['model_properties']={v.attrib.get('Name'):''.join(v.itertext()) for v in t.findall('.//Model/P')}
   if any(x in name for x in ['ReleaseInfo','mwcoreProperties.xml','coreProperties.xml','stateflow','modelWorkspace']):rec['other_xml'][name]=z.read(name).decode('utf8','replace')
  records.append(rec)
(OUT/'model_details.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')

pdf=ROOT/'programfile_gtec/gtec/gHIsys/Help/gHIsys-User-Manual.pdf'
r=PdfReader(pdf)
pages=[]
for i,p in enumerate(r.pages):
 tx=p.extract_text() or ''
 pages.append(dict(pdf_page=i+1,text=tx))
(OUT/'manual_pages.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2),encoding='utf8')
# A readable, selectively searchable local excerpt. Printed-page evidence remains in page text.
ranges=[(175,180),(270,286),(404,448),(533,576)]
tx=[]
for a,b in ranges:
 for i in range(a-1,b):tx.append(f'\n\n===== PDF PAGE {i+1} =====\n'+pages[i]['text'])
(OUT/'manual_excerpts.txt').write_text(''.join(tx),encoding='utf8')

paths=[ROOT/x for x in targets]+[pdf,ROOT/'g-tec/gHIsys/VirtualAndAugmentedReality/P300Mastermind/Simulink/PreprocessingBlock.mdl',ROOT/'hub/intent_control.py',ROOT/'protocols/execution-v1.md']
paths += list((ROOT/'g-tec/gHIsys/SSVEP/Batch').glob('*.m'))
paths += list((ROOT/'g-tec/gHIsys/Hybrid/Batch').glob('*.m'))
paths += list((ROOT/'g-tec/gHIsys/VirtualAndAugmentedReality/P300Mastermind/Simulink/Batch').glob('*.m'))
manifest=[dict(path=p.relative_to(ROOT).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size) for p in paths]
(OUT/'source_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(dict(models=len(records),pages=len(pages),sources=len(manifest)),ensure_ascii=False))
