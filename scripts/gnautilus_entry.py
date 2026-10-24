"""Stable PyInstaller entry; never enter GUI from multiprocessing children."""
from gnautilus_acquisition.__main__ import main
if __name__=='__main__':
    try:main()
    except Exception:
        import os,sys,json,traceback
        from pathlib import Path
        detail=traceback.format_exc();folder=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'EEGMovement/gNautilus'
        folder.mkdir(parents=True,exist_ok=True);log=folder/'startup-error.log';log.write_text(detail,encoding='utf-8')
        if '--report-path' in sys.argv:
            index=sys.argv.index('--report-path')
            if index+1<len(sys.argv):Path(sys.argv[index+1]).write_text(json.dumps(dict(startup_error=detail),ensure_ascii=False),encoding='utf-8')
        if sys.stderr:sys.stderr.write(detail)
        if not any(arg.startswith('--') for arg in sys.argv[1:]):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'程序启动失败，详细原因已保存到：\n'+str(log),'EEGMovement · 启动检查',0x10)
        raise SystemExit(1)
