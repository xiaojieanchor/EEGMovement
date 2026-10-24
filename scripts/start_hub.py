"""Start the local hub without a console window; never stores SSH credentials."""
import json,subprocess,sys,time,urllib.request,webbrowser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:8765'

def running():
    try:
        with urllib.request.urlopen(URL+'/api/status',timeout=2) as r:
            return json.load(r).get('project')=='EEGMovement'
    except Exception:return False

if __name__=='__main__':
    if '--worker' in sys.argv:
        import runpy
        runtime=ROOT/'hub/runtime';runtime.mkdir(exist_ok=True)
        sys.stdout=(runtime/'hub.log').open('a',encoding='utf-8',buffering=1);sys.stderr=sys.stdout
        sys.path.insert(0,str(ROOT));sys.argv=['hub.app'];runpy.run_module('hub.app',run_name='__main__');raise SystemExit()
    if not running():
        runtime=ROOT/'hub/runtime';runtime.mkdir(exist_ok=True)
        python=Path(sys.executable).with_name('pythonw.exe')
        if not python.exists():python=Path(sys.executable)
        if sys.platform=='win32':
            from windows_background import spawn
            spawn([str(python),str(Path(__file__).resolve()),'--worker'],ROOT)
        else:
            with (runtime/'hub.log').open('ab',buffering=0) as log:
                subprocess.Popen([str(python),'-m','hub.app'],cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        for _ in range(100):
            if running():break
            time.sleep(.15)
        else:raise RuntimeError('中枢未就绪，请检查 hub/runtime/hub.log 或本机 8765 端口')
    if '--no-open' not in sys.argv:webbrowser.open(URL)
    print('EEGMovement Hub: '+URL)
