"""Reuse the inspected Song/three-line-table renderer without editing prior report."""
from pathlib import Path
import importlib.util

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('gnautilus_report_renderer',HERE.parent/'evidence/render_report.py')
renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
renderer.BASE=HERE
renderer.MD=HERE/'迭代方案与采集评估手册_20261022.md'
renderer.PDF=renderer.MD.with_suffix('.pdf')
renderer.diagrams=lambda:None
if __name__=='__main__':
    (HERE/'evidence').mkdir(exist_ok=True)
    renderer.render()
