"""Start this workspace's hub and shared-core receiver, then open the local lab."""
import json,subprocess,sys,urllib.request,webbrowser
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if not (ROOT/'receiver/bin/EEGReceiver.exe').exists():
    subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/build_receiver.ps1')],check=True)
subprocess.run([sys.executable,str(ROOT/'scripts/start_hub.py'),'--no-open'],check=True,cwd=str(ROOT))
request=urllib.request.Request('http://127.0.0.1:8765/api/vr/receiver/start',data=b'{}',headers={'Content-Type':'application/json','X-EEGHub':'1'},method='POST')
with urllib.request.urlopen(request,timeout=5) as response:assert json.load(response)['ok']
if '--no-open' not in sys.argv:webbrowser.open('http://127.0.0.1:8765/lab')
print('Execution lab: http://127.0.0.1:8765/lab (C# shared core, NOT Unity engine)')
