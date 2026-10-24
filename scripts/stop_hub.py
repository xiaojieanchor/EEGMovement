"""Gracefully stop this project's local service after confirming its identity."""
import json,time,urllib.request
URL='http://127.0.0.1:8765'
with urllib.request.urlopen(URL+'/api/status',timeout=3) as response:
    if json.load(response).get('project')!='EEGMovement':raise RuntimeError('Port does not belong to EEGMovement')
request=urllib.request.Request(URL+'/api/shutdown',data=b'{}',headers={'Content-Type':'application/json','X-EEGHub':'1'},method='POST')
with urllib.request.urlopen(request,timeout=3) as response:
    assert json.load(response)['ok']
for _ in range(50):
    try:
        with urllib.request.urlopen(URL+'/api/status',timeout=.3) as response:pass
    except Exception:break
    time.sleep(.1)
else:raise RuntimeError('Hub did not finish shutting down')
print('EEGMovement local hub stopped; remote project worker is retained for reuse.')
