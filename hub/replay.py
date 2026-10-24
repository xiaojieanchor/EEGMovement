"""Read only recordings created in this project's session directory."""
import json,math
from pathlib import Path

def session_path(directory,name):
    root=Path(directory).resolve()
    if not isinstance(name,str) or Path(name).name!=name or not name.endswith('.jsonl'):
        raise ValueError('请选择会话列表中的 JSONL 文件')
    path=(root/name).resolve()
    if path.parent!=root or not path.is_file():raise ValueError('会话文件不存在或越过会话目录')
    return path

def read_session(path):
    with Path(path).open(encoding='utf-8') as f:
        header=json.loads(next(f))
        if header.get('kind')!='session' or header.get('sfreq')!=250 or len(header.get('channels',[]))!=7:
            raise ValueError('仅支持本中枢的七通道 250 Hz 会话')
        for line in f:
            if not line.strip():continue
            row=json.loads(line)
            if row.get('kind')=='marker':yield row;continue
            if row.get('kind')!='eeg':continue
            samples=row.get('samples_uv',[])
            if not 1<=len(samples)<=250 or any(len(x)!=7 or any(not math.isfinite(float(v)) for v in x) for x in samples):
                raise ValueError('会话样本不是有限数值的 Nx7 数组')
            yield row

def list_sessions(directory):
    root=Path(directory)
    if not root.exists():return []
    result=[]
    for path in sorted(root.glob('*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)[:200]:
        try:
            with path.open(encoding='utf-8') as f:meta=json.loads(next(f))
            if meta.get('kind')=='session':
                result.append(dict(name=path.name,bytes=path.stat().st_size,started_at=meta.get('started_at'),source=meta.get('source')))
        except (OSError,ValueError,StopIteration):continue
    return result
