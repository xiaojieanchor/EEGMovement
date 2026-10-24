"""Hidden local-console password entry; credential stays in memory only."""
import getpass,json,urllib.request

if __name__=='__main__':
    password=getpass.getpass('SSH password (memory only): ')
    request=urllib.request.Request('http://127.0.0.1:8765/api/server/connect',data=json.dumps({'password':password}).encode(),headers={'Content-Type':'application/json','X-EEGHub':'1'},method='POST')
    with urllib.request.urlopen(request,timeout=5) as response:
        assert json.load(response)['ok']
    password=None;request=None
    print('Connection requested. Check the hub for verified GPU readiness.')
