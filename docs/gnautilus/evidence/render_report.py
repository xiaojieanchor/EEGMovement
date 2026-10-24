"""Render the approved Chinese Markdown report as a plain, embedded-Song PDF."""
from pathlib import Path
import html, json, re
from urllib.parse import quote
from markdown_it import MarkdownIt
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                               Image, PageBreak, KeepTogether, HRFlowable)
from reportlab.platypus.tableofcontents import TableOfContents
from PIL import Image as PILImage, ImageDraw, ImageFont

BASE=Path(__file__).resolve().parents[1]
MD=BASE/'gNautilus_32通道与Simulink迁移分析_20261022.md'
PDF=MD.with_suffix('.pdf')
FONT='C:/Windows/Fonts/simsun.ttc'

def diagrams():
    dest=BASE/'evidence/figures';dest.mkdir(parents=True,exist_ok=True)
    f=ImageFont.truetype(FONT,31);small=ImageFont.truetype(FONT,26)
    for name,title,rows in [
        ('proposed_pipeline','建议的新设备实时数据流（架构建议，尚未实施）',[
          ['g.Nautilus／厂商 API','带身份的原始记录','因果处理＋新设备质量判断'],
          ['运动／眼动特征与模型','拒识＋连续状态决策','现有中枢与 VR 执行回执']]),
        ('validation_split','训练、选择与验收的数据边界（建议实验设计）',[
          ['训练会话 A／B','拟合滤波空间／标准化／分类器','仅使用训练数据学习参数'],
          ['开发会话 C','选择窗口／模型／拒识阈值','固定全部处理与决策规则'],
          ['新日期＋新佩戴会话 D','连续盲测＋独立 VR 闭环','报告识别／误启动／延迟']])]:
        w=1800;h=190+len(rows)*210
        im=PILImage.new('RGB',(w,h),'white');d=ImageDraw.Draw(im)
        d.text((w/2,55),title,font=f,fill='black',anchor='mm')
        for ri,row in enumerate(rows):
            y=140+ri*210
            for ci,txt in enumerate(row):
                x=45+ci*590;box=(x,y,x+530,y+125);d.rectangle(box,outline='black',width=3)
                # two lines where a long label benefits from more room
                parts=[txt] if d.textlength(txt,font=small)<=480 else [txt[:12],txt[12:]]
                for ti,part in enumerate(parts):d.text((x+265,y+62+(ti-(len(parts)-1)/2)*39),part,font=small,fill='black',anchor='mm')
                if ci<2:
                    xx=x+540;yy=y+62;d.line((xx,yy,xx+42,yy),fill='black',width=3)
                    d.polygon([(xx+42,yy),(xx+29,yy-7),(xx+29,yy+7)],fill='black')
            if name=='proposed_pipeline' and ri==0:
                d.line((1575,y+135,1575,y+167,310,y+167,310,y+200),fill='black',width=3)
                d.polygon([(310,y+200),(303,y+188),(317,y+188)],fill='black')
        im.save(dest/f'{name}.png')

def inline(tokens):
    out=[]
    for t in tokens or []:
        typ=t.type
        if typ=='text':out.append(html.escape(t.content))
        elif typ=='code_inline':out.append('<font name="Song" size="9">'+html.escape(t.content)+'</font>')
        elif typ=='strong_open':out.append('<b>')
        elif typ=='strong_close':out.append('</b>')
        elif typ=='em_open':out.append('<i>')
        elif typ=='em_close':out.append('</i>')
        elif typ in ['softbreak','hardbreak']:out.append('<br/>' if typ=='hardbreak' else ' ')
        elif typ=='link_open':
            href=t.attrGet('href') or ''
            if href.startswith('#'):out.append('')
            else:
                if re.match(r'^[A-Za-z]:[/\\]',href):href=Path(href).resolve().as_uri()
                elif not re.match(r'^[a-zA-Z]+:',href):href=(BASE/href).resolve().as_uri()
                out.append('<link href="'+html.escape(href,quote=True)+'" color="black">')
        elif typ=='link_close':
            # closing anchor is suppressed when the matching open was internal.
            balance=''.join(out).count('<link ')-''.join(out).count('</link>')
            if balance>0:out.append('</link>')
        elif typ=='html_inline':out.append(html.escape(t.content))
    return ''.join(out)

class ReportDoc(SimpleDocTemplate):
    def afterFlowable(self,flowable):
        if isinstance(flowable,Paragraph) and getattr(flowable,'outline_level',None) is not None:
            level=flowable.outline_level;text=flowable.getPlainText();key=f'h{self.seq.nextf("heading")}';self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text,key,level,False)
            if level<=1:self.notify('TOCEntry',(level,text,self.page,key))

def render():
    pdfmetrics.registerFont(TTFont('Song',FONT,subfontIndex=0))
    pdfmetrics.registerFontFamily('Song',normal='Song',bold='Song',italic='Song',boldItalic='Song')
    diagrams()
    body=ParagraphStyle('body',fontName='Song',fontSize=10.5,leading=17.2,spaceAfter=6,
                        wordWrap='CJK',splitLongWords=True,allowWidows=0,allowOrphans=0)
    styles={
      'p':body,
      'h1':ParagraphStyle('h1',parent=body,fontSize=20,leading=28,spaceBefore=14,spaceAfter=14,keepWithNext=1),
      'h2':ParagraphStyle('h2',parent=body,fontSize=15.5,leading=22,spaceBefore=18,spaceAfter=9,keepWithNext=1),
      'h3':ParagraphStyle('h3',parent=body,fontSize=12.5,leading=19,spaceBefore=12,spaceAfter=6,keepWithNext=1),
      'h4':ParagraphStyle('h4',parent=body,fontSize=11.2,leading=17,spaceBefore=8,spaceAfter=5,keepWithNext=1),
      'table':ParagraphStyle('table',parent=body,fontSize=8.7,leading=12.5,spaceAfter=0,allowWidows=1,allowOrphans=1),
      'code':ParagraphStyle('code',parent=body,fontSize=8.8,leading=13,spaceBefore=4,spaceAfter=7),
      'caption':ParagraphStyle('caption',parent=body,fontSize=9,leading=14,alignment=TA_CENTER,spaceAfter=10)}
    tokens=MarkdownIt('commonmark',{'html':False}).enable('table').parse(MD.read_text('utf-8'))
    story=[];i=0;list_depth=0;list_numbers=[];table_records=[];image_records=[]
    inserted_toc=False
    while i<len(tokens):
        t=tokens[i]
        if t.type=='heading_open':
            level=int(t.tag[1]);txt=inline(tokens[i+1].children);plain=tokens[i+1].content
            if level==2 and not inserted_toc:
                story.append(Spacer(1,12));story.append(Paragraph('目录',styles['h2']))
                toc=TableOfContents();toc.levelStyles=[ParagraphStyle('toc1',parent=body,fontSize=10.2,leading=17,leftIndent=0,firstLineIndent=0),ParagraphStyle('toc2',parent=body,fontSize=9.2,leading=15,leftIndent=13)]
                story.append(toc);story.append(PageBreak());inserted_toc=True
            elif level==2 and not plain.startswith('附录'):
                story.append(Spacer(1,3))
            p=Paragraph(txt,styles['h'+str(min(level,4))]);p.outline_level=min(level-1,2)
            story.append(p);i+=3;continue
        if t.type=='paragraph_open':
            inn=tokens[i+1]
            children=inn.children or []
            if len(children)==1 and children[0].type=='image':
                imt=children[0];path=(BASE/imt.attrGet('src')).resolve()
                with PILImage.open(path) as im:iw,ih=im.size
                maxw=A4[0]-100;w=min(maxw,iw*0.4);h=w*ih/iw
                story.append(Image(str(path),width=w,height=h));story.append(Paragraph(html.escape(imt.content),styles['caption']))
                image_records.append(str(path))
            else:
                prefix=''
                if i>0 and tokens[i-1].type=='list_item_open':
                    if list_numbers and list_numbers[-1] is not None:
                        prefix=str(list_numbers[-1])+'. ';list_numbers[-1]+=1
                    else:prefix='• '
                st=ParagraphStyle('listp',parent=body,leftIndent=list_depth*12,firstLineIndent=0) if list_depth else body
                story.append(Paragraph(prefix+inline(children),st))
            i+=3;continue
        if t.type in ['bullet_list_open','ordered_list_open']:
            list_depth+=1;list_numbers.append(int(t.attrGet('start') or 1) if t.type=='ordered_list_open' else None)
        elif t.type in ['bullet_list_close','ordered_list_close']:
            list_depth-=1;list_numbers.pop()
        elif t.type=='table_open':
            rows=[];row=[];header=True;i+=1
            while tokens[i].type!='table_close':
                if tokens[i].type=='tr_open':row=[]
                elif tokens[i].type=='inline':row.append(Paragraph(inline(tokens[i].children),styles['table']))
                elif tokens[i].type=='tr_close':rows.append(row)
                i+=1
            n=max(len(r) for r in rows);width=A4[0]-100
            weights={2:[.29,.71],3:[.20,.37,.43],4:[.15,.27,.29,.29],5:[.10,.23,.24,.21,.22],6:[.08,.18,.20,.18,.18,.18]}.get(n,[1/n]*n)
            if n==3 and any('编号' in c.getPlainText() for c in rows[0]):weights=[.12,.23,.65]
            tab=Table(rows,colWidths=[width*w for w in weights],repeatRows=1,hAlign='LEFT',splitByRow=1)
            tab.setStyle(TableStyle([
               ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),
               ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
               ('LINEABOVE',(0,0),(-1,0),.9,colors.black),('LINEBELOW',(0,0),(-1,0),.5,colors.black),
               ('LINEBELOW',(0,-1),(-1,-1),.9,colors.black)]))
            story.extend([Spacer(1,5),tab,Spacer(1,10)]);table_records.append({'rows':len(rows),'columns':n})
        elif t.type in ['fence','code_block']:
            text='<br/>'.join(html.escape(ln).replace('  ','&#160;&#160;') or '&#160;' for ln in t.content.rstrip().splitlines())
            story.append(Paragraph(text,styles['code']))
        elif t.type=='hr':story.append(HRFlowable(width='100%',thickness=.5,color=colors.black,spaceBefore=8,spaceAfter=8))
        i+=1
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont('Song',8.3)
        canvas.drawString(50,25,'g.Nautilus 32通道与Simulink迁移分析 | 2026-10-22')
        canvas.drawRightString(A4[0]-50,25,str(doc.page));canvas.restoreState()
    doc=ReportDoc(str(PDF),pagesize=A4,leftMargin=50,rightMargin=50,topMargin=44,bottomMargin=43,
                  title='g.Nautilus 32通道与Simulink迁移分析',author='EEGMovement项目技术分析',allowSplitting=1)
    doc.multiBuild(story,onFirstPage=footer,onLaterPages=footer)
    (BASE/'evidence/pdf_layout.json').write_text(json.dumps({'font':FONT,'table_style':'black three-line; no fill; no vertical rules',
       'body_pt':10.5,'tables':table_records,'images':image_records,'markdown':MD.name,'pdf':PDF.name},ensure_ascii=False,indent=2),encoding='utf-8')
    print(PDF)

if __name__=='__main__':render()
