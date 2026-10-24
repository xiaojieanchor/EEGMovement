"""Index new-device records without assigning training or independent-test roles."""
import json
from pathlib import Path
from .storage import atomic_json


def build_catalog(root):
    root=Path(root);entries=[]
    if root.exists():
        for path in sorted(root.glob('*/sessions/*/descriptor.json'))+sorted(root.glob('*/streams/*/*/descriptor.json')):
            d=json.loads(path.read_text('utf-8'));closed=path.parent/'closed.json'
            report=json.loads(closed.read_text('utf-8')) if closed.exists() else None
            entries.append(dict(path=str(path.parent.relative_to(root)),stream_id=d['stream_id'],source_kind=d['source_kind'],
                sampling_rate=d['sampling_rate'],columns=len(d['channels']),wearing_id=d.get('wearing_id'),role='unassigned',
                closed=bool(report),samples=report.get('samples') if report else None,files=report.get('files') if report else None,
                formal_training_ready=False,hardware_acceptance=False))
    result=dict(schema='gnautilus.catalog.v1',records=entries)
    atomic_json(root/'catalog/catalog.json',result);return result
