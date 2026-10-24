"""Render every delivered page and audit text geometry/font embedding."""
from pathlib import Path
import json,math
import fitz
from PIL import Image,ImageDraw

HERE=Path(__file__).resolve().parent
DEST=HERE/'verification/pdf-pages'
DEST.mkdir(parents=True,exist_ok=True)
FILES=[HERE/'Python API与独立采集端接入说明_20261022.pdf',HERE/'新增模型结构与迁移分析卡片_20261022.pdf',
       HERE.parent/'gNautilus_32通道与Simulink迁移分析_20261022.pdf',HERE.parent/'iteration_20261022/迭代方案与采集评估手册_20261022.pdf']


def main():
    results=[];thumbs=[]
    for fi,path in enumerate(FILES):
        document=fitz.open(path);pages=[]
        for pi,page in enumerate(document):
            pix=page.get_pixmap(matrix=fitz.Matrix(1.15,1.15),alpha=False)
            dest=DEST/f'{fi+1}-{pi+1:03}.png';pix.save(dest)
            text=page.get_text();outside=[]
            for b in page.get_text('dict')['blocks']:
                for line in b.get('lines',[]):
                    for span in line['spans']:
                        x0,y0,x1,y1=span['bbox']
                        if x0<0 or y0<0 or x1>page.rect.width+1 or y1>page.rect.height+1:outside.append(span['text'][:80])
            pages.append(dict(page=pi+1,characters=len(text),outside=outside,embedded_song=any('SimSun' in f[3] and f[1]=='ttf' for f in page.get_fonts())))
            im=Image.open(dest).convert('RGB');im.thumbnail((235,333))
            th=Image.new('RGB',(255,360),'#e9e9e9');th.paste(im,((255-im.width)//2,15));ImageDraw.Draw(th).text((10,345),f'{fi+1} / {pi+1}',fill='black');thumbs.append(th)
        results.append(dict(file=str(path),page_count=len(document),pages=pages))
    contacts=[]
    for begin in range(0,len(thumbs),24):
        chunk=thumbs[begin:begin+24];sheet=Image.new('RGB',(1020,360*math.ceil(len(chunk)/4)),'white')
        for i,im in enumerate(chunk):sheet.paste(im,((i%4)*255,(i//4)*360))
        path=DEST/f'contact-{begin//24+1:02}.png';sheet.save(path);contacts.append(str(path))
    result=dict(files=results,contact_sheets=contacts,all_pages_rendered=True,all_text_in_bounds=all(not p['outside'] for f in results for p in f['pages']),
                all_pages_song=all(p['embedded_song'] for f in results for p in f['pages']))
    (HERE/'verification/pdf_qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(pages=sum(f['page_count'] for f in results),contacts=len(contacts),in_bounds=result['all_text_in_bounds'],song=result['all_pages_song'])))


if __name__=='__main__':main()
