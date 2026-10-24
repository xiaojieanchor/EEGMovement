"""Check distributables against actual private credential values without printing them."""
import hashlib
import json
from pathlib import Path
import zipfile
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_public_release import APP_VERSION,QUEST_VERSION


def main():
    values=set()
    def walk(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in ('secret','auth_secret','admin_key','key') and isinstance(item,str) and len(item)>=32:
                    values.add(item.encode())
                walk(item)
        elif isinstance(value,list):
            for item in value:walk(item)
    inputs=[*(ROOT/'hub/runtime/relay_private').rglob('*.json'),*(ROOT/'hub/runtime/relay_enrollments').glob('*.json')]
    for path in inputs:walk(json.loads(path.read_text(encoding='utf-8-sig')))
    forbidden=[];results=[]
    for name in (f'EEGMovement-Windows-x64-{APP_VERSION}.zip',f'EEGMovement-Quest-{QUEST_VERSION}.apk',f'EEGMovement-Relay-Source-{APP_VERSION}-Quest-{QUEST_VERSION}.zip'):
        path=ROOT/'outputs/relay_20261023/release'/name
        with zipfile.ZipFile(path) as archive:
            entries=archive.namelist()
            for entry in entries:
                body=archive.read(entry)
                parts=Path(entry).parts
                if any(v in body for v in values) or any(p in parts for p in ('relay_private','relay_enrollments')) or Path(entry).name in ('server.key','cache.key','release-signing.key'):
                    forbidden.append(name+':'+entry)
        results.append(dict(name=name,entries=len(entries),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    result=dict(actual_secret_values_checked=len(values),forbidden_entries=forbidden,packages=results,passed=not forbidden)
    (ROOT/'docs/gnautilus/relay_20261023_followup/package-secret-check.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    if forbidden:raise SystemExit(1)


if __name__=='__main__':main()
