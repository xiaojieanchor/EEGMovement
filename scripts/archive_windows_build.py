"""Freeze Windows build inputs before switching Unity's target platform."""
import hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    build=json.loads(Path('G:/Unity/build-status.json').read_text(encoding='utf-8-sig'))
    assert build['success'] and build['source_snapshot_unchanged'] and build['phase']=='succeeded'
    sources=build['source_hashes'];snapshot=Path('G:/EEGMovement/SourceSnapshots')/('Windows-'+build['build_id']+'.zip')
    for name,sha in sources.items():
        p=(ROOT/name).resolve()
        if not p.is_relative_to(ROOT/'unity/EEGMovement') or digest(p)!=sha:raise ValueError('Windows source identity mismatch: '+name)
    with zipfile.ZipFile(snapshot,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(sources):archive.write(ROOT/name,name)
        archive.writestr('_build.json',json.dumps(build,indent=2))
    with zipfile.ZipFile(snapshot) as archive:
        assert archive.testzip() is None
        for name,sha in sources.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=sha or digest(ROOT/name)!=sha:raise ValueError('Source changed during archiving')
    build['source_snapshot']=dict(path=str(snapshot),sha256=digest(snapshot));(ROOT/'outputs/windows_build_validation.json').write_text(json.dumps(build,indent=2),encoding='utf-8')
    print(json.dumps(dict(build_id=build['build_id'],source_files=len(sources),snapshot=str(snapshot))))
if __name__=='__main__':main()
