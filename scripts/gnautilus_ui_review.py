"""Temporary loopback UI inspection server. No device, model, ingestion or recording."""
import json
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hub import app


if __name__=='__main__':
    app.HUB=app.Hub(json.loads((app.ROOT/'hub/config.json').read_text('utf-8')))
    server=ThreadingHTTPServer(('127.0.0.1',18765),app.Handler)
    print('UI-only review http://127.0.0.1:18765; no device/ingest/VR sockets started',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
