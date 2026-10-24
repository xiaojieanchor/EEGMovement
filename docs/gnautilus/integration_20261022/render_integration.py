"""Use the existing Song/three-line renderer while keeping prior evidence immutable."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parent/'evidence/render_report.py'


def render(md):
    code=SOURCE.read_text('utf-8').replace("(BASE/'evidence/pdf_layout.json')",'LAYOUT_PATH')
    code=code.replace("'g.Nautilus 32通道与Simulink迁移分析 | 2026-10-22'",'DOCUMENT_LABEL')
    code=code.replace("title='g.Nautilus 32通道与Simulink迁移分析'",'title=DOCUMENT_LABEL')
    namespace={'__file__':str(SOURCE),'__name__':'integration_renderer'}
    exec(compile(code,str(SOURCE),'exec'),namespace)
    namespace.update(BASE=md.parent,MD=md,PDF=md.with_suffix('.pdf'),DOCUMENT_LABEL=md.stem,
        LAYOUT_PATH=HERE/'verification'/(md.stem+'.layout.json'),diagrams=lambda:None)
    namespace['render']()


if __name__=='__main__':
    for md in [HERE/'Python API与独立采集端接入说明_20261022.md',HERE/'新增模型结构与迁移分析卡片_20261022.md',
        HERE.parent/'gNautilus_32通道与Simulink迁移分析_20261022.md',HERE.parent/'iteration_20261022/迭代方案与采集评估手册_20261022.md']:
        render(md)
