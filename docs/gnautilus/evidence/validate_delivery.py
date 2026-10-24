"""Verify document coverage, source preservation, PDF text/fonts/layout; render all pages."""
from pathlib import Path
import concurrent.futures, hashlib, json, re
from urllib.parse import unquote
import fitz
from PIL import Image, ImageDraw, ImageFont
from markdown_it import MarkdownIt
from reportlab.pdfbase.ttfonts import TTFont

BASE=Path(__file__).resolve().parents[1]
EV=BASE/'evidence';ROOT=BASE.parents[1]

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    md=BASE/'gNautilus_32通道与Simulink迁移分析_20261022.md';pdf=md.with_suffix('.pdf')
    content=md.read_text('utf-8');models=json.loads((EV/'model_index.json').read_text('utf-8'))
    current=json.loads((EV/'source_inventory.json').read_text('utf-8'))
    def check_file(record):
        p=ROOT/record['path']
        return record['path'] if not p.is_file() or digest(p)!=record['sha256'] else None
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        changed_sources=[p for p in pool.map(check_file,current['files']) if p]
        baseline=json.loads((EV/'runtime_baseline.json').read_text('utf-8'))
        changed_runtime=[p for p in pool.map(check_file,baseline) if p]
    layouts=json.loads((EV/'device/montages.json').read_text('utf-8'))['layouts']
    montage_checks={k:{'count':len(v['positions']),'unique':len(set(v['positions'])),
                       'has_Cz':'Cz' in v['positions']} for k,v in layouts.items()}
    token=MarkdownIt('commonmark').enable('table').parse(content)
    broken_links=[]
    for t in token:
        for c in t.children or []:
            if c.type not in ['link_open','image']:continue
            href=c.attrGet('href') if c.type=='link_open' else c.attrGet('src')
            if not href or href.startswith(('http:','https:','#','mailto:')):continue
            p=Path(href) if re.match(r'^[A-Za-z]:[/\\]',href) else BASE/href
            if not p.exists():broken_links.append(href)
    font=TTFont('SongValidation','C:/Windows/Fonts/simsun.ttc',subfontIndex=0)
    unknown=sorted({c for c in content if ord(c)>32 and ord(c) not in font.face.charToGlyph})
    doc=fitz.open(pdf);qa=EV/'qa';(qa/'pages').mkdir(parents=True,exist_ok=True)
    outside=[];sparse=[];pageinfo=[];texts=[];wrong_fonts=[];embedded=[]
    for i,page in enumerate(doc):
        text=page.get_text();texts.append(text)
        page.get_pixmap(matrix=fitz.Matrix(1.15,1.15),alpha=False).save(qa/'pages'/f'{i+1:03d}.png')
        for b in page.get_text('dict')['blocks']:
            if b.get('type')!=0:continue
            for line in b['lines']:
                for s in line['spans']:
                    x0,y0,x1,y1=s['bbox']
                    footer=(y0>803 and s['size']<9)
                    if not footer and (x0<43 or x1>page.rect.width-43 or y0<29 or y1>799):
                        outside.append({'page':i+1,'bbox':s['bbox'],'text':s['text'][:100]})
                    if not footer and 'SimSun' not in s['font']:wrong_fonts.append({'page':i+1,'font':s['font'],'text':s['text'][:40]})
        for f in page.get_fonts():
            if 'SimSun' in f[3] and f[0] not in embedded:
                assert len(doc.extract_font(f[0])[3])>0
                embedded.append(f[0])
        if i>0 and len(text)<120:sparse.append(i+1)
        pageinfo.append({'page':i+1,'text_characters':len(text),'links':len(page.get_links()),'images':len(page.get_images())})
    label=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',15)
    sheets=[]
    for start in range(0,len(doc),12):
        sheet=Image.new('RGB',(900,1800),'#d8d8d8');draw=ImageDraw.Draw(sheet)
        for j,n in enumerate(range(start,min(start+12,len(doc)))):
            im=Image.open(qa/'pages'/f'{n+1:03d}.png').convert('RGB');im.thumbnail((286,410))
            x=(j%3)*300+(300-im.width)//2;y=(j//3)*450+25
            sheet.paste(im,(x,y));draw.text(((j%3)*300+12,(j//3)*450+4),f'Page {n+1}',font=label,fill='black')
        dest=qa/f'contact_{start+1:03d}_{min(start+12,len(doc)):03d}.png';sheet.save(dest);sheets.append(dest.relative_to(BASE).as_posix())
    alltext='\n'.join(texts);(qa/'pdf_text.txt').write_text(alltext,encoding='utf-8')
    cards=re.findall(r'^### (M\d{3}) ·',content,re.M)
    checks={
      'source_files_unchanged':not changed_sources,'runtime_sources_unchanged':not changed_runtime,
      'registered_sources_match':current['prior_registry_comparison']=={'changed':[],'missing':[],'added':[]},
      'all_84_model_cards':set(cards)=={m['id'] for m in models} and len(cards)==84,
      'models_56_sltx_27_slx_1_mdl':sum(m['path'].endswith('.sltx') for m in models)==56 and sum(m['path'].endswith('.slx') for m in models)==27 and sum(m['path'].endswith('.mdl') for m in models)==1,
      'both_montages_32_unique':all(v['count']==32 and v['unique']==32 and v['has_Cz'] for v in montage_checks.values()),
      'all_local_report_links_exist':not broken_links,
      'song_covers_all_text':not unknown,'song_body_embedded':bool(embedded) and not wrong_fonts,
      'pdf_no_replacement_characters':'\ufffd' not in alltext,
      'pdf_body_within_margins':not outside,'pdf_no_near_empty_body_pages':not sparse,
      'all_model_ids_in_pdf':all(m['id'] in alltext for m in models),
      'pdf_has_bookmarks':len(doc.get_toc())>100,
      'review_findings_resolved_in_text':'计入覆盖率总时间' in content and '三类重复排除通道' in content and '可保留冻结CSP矩阵' in content,
      'explicit_static_not_hardware_acceptance':'没有执行厂商安装指令' in content and '图示为建议架构，未在本轮实现' in content,
    }
    result={'date':'2026-10-22','checks':checks,'all_automated_checks_pass':all(checks.values()),
      'source_count':len(current['files']),'runtime_baseline_count':len(baseline),
      'changed_sources':changed_sources,'changed_runtime':changed_runtime,'montages':montage_checks,
      'local_broken_links':broken_links,'missing_glyphs':unknown,'wrong_body_fonts':wrong_fonts,
      'outside_body_spans':outside,'near_empty_body_pages':sparse,'pdf_pages':len(doc),
      'pdf_text_characters':len(alltext),'markdown_characters':len(content),'page_info':pageinfo,
      'contact_sheets':sheets,'embedded_song_font_subsets':len(embedded),
      'outputs':{md.name:{'sha256':digest(md),'bytes':md.stat().st_size},pdf.name:{'sha256':digest(pdf),'bytes':pdf.stat().st_size}},
      'verification_scope':'static source/document/layout checks; not MATLAB execution, EEG model performance or hardware/VR acceptance'}
    (BASE/'final_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['checks','all_automated_checks_pass','pdf_pages','markdown_characters','missing_glyphs','local_broken_links']},ensure_ascii=False))
    if not all(checks.values()):raise SystemExit(1)

if __name__=='__main__':main()
