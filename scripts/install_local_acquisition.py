"""Install a locally built workbench without modifying signed-update trust/state.

This developer command takes a local build directory, not a remote manifest.
Remote updates continue to require their existing release signature.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from eeg_relay.update import activate,file_hash
from scripts.gnautilus_launcher import close_installed_app,health,install_root


def install(source,root,*,start_receiving=False,status_path=None):
    source=Path(source).resolve(strict=True);root=Path(root).resolve()
    if not source.is_dir() or not (source/'EEGMovement-gNautilus.exe').is_file():
        raise ValueError('请选择包含EXE和_internal的完整本地构建目录')
    if not (source/'_internal').is_dir():raise ValueError('构建目录缺少_internal')
    if source.is_relative_to(root) or root.is_relative_to(source):raise ValueError('构建来源与安装根目录不能互相包含')
    from gnautilus_acquisition import __version__
    paths=list(source.rglob('*'))
    if any(p.is_symlink() for p in paths):raise ValueError('本地构建目录不允许符号链接')
    hashes={p.relative_to(source).as_posix():file_hash(p) for p in paths if p.is_file()}
    close_installed_app(root)
    root.mkdir(parents=True,exist_ok=True)
    directory='versions/local-'+__version__+'-'+uuid.uuid4().hex[:8]
    candidate=root/directory
    shutil.copytree(source,candidate)
    if any(file_hash(candidate/name)!=digest for name,digest in hashes.items()):raise ValueError('本地复制文件校验失败，未切换版本')
    # Keep the previous current.json for rollback, including signed release state.
    activate(root,__version__,directory,lambda p:health(root,p,__version__,start_receiving=start_receiving,status_path=status_path))
    launcher=root/'EEGMovement-Launcher.exe'
    target=launcher if launcher.is_file() else candidate/'EEGMovement-gNautilus.exe'
    desktop=Path.home()/'Desktop/EEGMovement采集工作台.cmd'
    desktop.write_text('@start "" "'+str(target)+'"'+chr(13)+chr(10),encoding='utf-8')
    return dict(version=__version__,directory=directory,files_verified=len(hashes),
                start_receiving_requested=start_receiving,signed_update_state_modified=False,shortcut=str(desktop))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',default=str(ROOT/'outputs/gnautilus_release/EEGMovement-gNautilus'))
    parser.add_argument('--root',default=str(install_root()))
    parser.add_argument('--start-receiving',action='store_true')
    parser.add_argument('--status-file')
    args=parser.parse_args()
    print(json.dumps(install(args.source_dir,args.root,start_receiving=args.start_receiving,status_path=args.status_file),ensure_ascii=False))


if __name__=='__main__':main()
