"""Direct Hotspot / LAN WebSocket ingest & discovery for EEG acquisition client."""
import datetime
import json
import math
import secrets
import socket
import threading
import time
from pathlib import Path
from websockets.sync.server import serve
from eeg_relay.protocol import encoded, pack, unpack
from gnautilus_acquisition.contract import HEX32, json_bytes, validate_block, validate_descriptor
from gnautilus_acquisition.storage import StreamJournal


DEFAULT_LAN_WS_PORT = 8768
LAN_WS_PATH = '/eeg/v1/lan'


class LanHubServer:
    def __init__(self, runtime, *, host='0.0.0.0', port=DEFAULT_LAN_WS_PORT):
        self.runtime = runtime
        self.host = host
        self.requested_port = port
        self.port = port
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.server = None
        self.udp_sock = None
        self.tickets = {}
        self.client_reports = {}
        self.active_clients = 0

    def start(self):
        if self.requested_port is None or self.requested_port < 0:
            return self
        ports = [self.requested_port] if self.requested_port == 0 else [self.requested_port, 0]
        for candidate in ports:
            try:
                self.server = serve(
                    self._handle_ws,
                    self.host,
                    candidate,
                    process_request=self._handle_http,
                    max_size=8 * 1024 * 1024,
                    max_queue=128,
                    compression=None,
                    ping_interval=10,
                    ping_timeout=20,
                    close_timeout=1,
                    open_timeout=5,
                )
                self.port = self.server.socket.getsockname()[1]
                break
            except OSError:
                self.server = None
        if self.server is None:
            return self
        threading.Thread(target=self.server.serve_forever, daemon=True, name='Hub LAN WebSocket').start()
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind((self.host, self.port))
            udp.settimeout(0.3)
            self.udp_sock = udp
            threading.Thread(target=self._serve_udp, daemon=True, name='Hub LAN UDP discovery').start()
        except OSError:
            self.udp_sock = None
        return self

    def _serve_udp(self):
        while not self.stop_event.is_set() and self.udp_sock:
            try:
                raw, addr = self.udp_sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(raw.decode('utf-8', errors='replace'))
                if isinstance(msg, dict) and msg.get('discover') == 'eegmovement_hub_v1':
                    reply = encoded(dict(
                        service='eegmovement_hub_v1',
                        schema='gnautilus.lan_ws.v1',
                        port=self.port,
                        ws_path=LAN_WS_PATH,
                    ))
                    self.udp_sock.sendto(reply, addr)
            except Exception:
                pass

    def _handle_http(self, connection, request):
        if request.path in ('/eeg/v1/lan/info', '/eeg/health'):
            body = json.dumps(dict(
                service='eegmovement_hub_v1',
                schema='gnautilus.lan_ws.v1',
                status='ok',
                port=self.port,
                ws_path=LAN_WS_PATH,
            ))
            reply = connection.respond(200, body)
            reply.headers['Content-Type'] = 'application/json'
            return reply
        if request.path != LAN_WS_PATH:
            return connection.respond(404, 'Not found')

    def _issue_ticket(self, agent_id):
        ticket = secrets.token_hex(16)
        now = time.monotonic()
        with self.lock:
            self.tickets[(agent_id, ticket)] = now
            self.tickets = {k: v for k, v in self.tickets.items() if now - v < 2.0}
        return ticket, now

    def _ensure_journal(self, agent_id, d):
        service = self.runtime.service
        with service.lock:
            identity = (agent_id, d['stream_id'])
            j = service.journals.get(identity)
            if j is None:
                data_root = service.root / 'data/gnautilus'
                existing = list(data_root.glob(f'*/streams/{agent_id}/{d["stream_id"]}/descriptor.json'))
                if len(existing) > 1:
                    raise ValueError('Multiple dated journals for one stream')
                path = existing[0].parent if existing else data_root / datetime.date.today().isoformat() / 'streams' / agent_id / d['stream_id']
                j = StreamJournal(path, d)
                service.journals[identity] = j
            elif json_bytes(j.descriptor) != json_bytes(d):
                raise ValueError('Stream descriptor changed')
            if service.selected_agent is None:
                service.selected_agent = agent_id
            service.status.update(enabled=True, connected=True, descriptor=d, state='waiting_samples', agent_id=agent_id, transport='lan_ws')
            return j

    def _record_client_status(self, agent_id, data):
        service = self.runtime.service
        with service.lock:
            service.status.update(agent_health={k: data.get(k) for k in ('sdk_state', 'disk_state', 'queue_blocks', 'error')})
        if 'client_status' in data:
            from gnautilus_acquisition.telemetry import sanitize
            try:
                report = sanitize(data['client_status'])
                now = time.monotonic()
                with self.lock:
                    self.client_reports[agent_id] = dict(reported=report, received=now, lan_ws=True)
                relay = getattr(self.runtime.hub, 'public_relay', None)
                if relay is not None and hasattr(relay, 'client_reports'):
                    with relay.lock:
                        relay.client_reports[agent_id] = dict(reported=report, received=now, lan_ws=True)
            except (ValueError, TypeError):
                pass

    def _handle_ws(self, ws):
        agent_id = None
        descriptor = None
        with self.lock:
            self.active_clients += 1
        try:
            try:
                sock = getattr(ws, 'socket', None)
                if sock is not None:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            for raw in ws:
                if self.stop_event.is_set():
                    break
                if isinstance(raw, str):
                    h = json.loads(raw)
                    body = b''
                else:
                    h, body = unpack(raw)
                kind = h.get('kind')
                raw_agent = str(h.get('agent_id') or agent_id or '').lower()
                if HEX32.fullmatch(raw_agent):
                    agent_id = raw_agent
                elif agent_id is None:
                    agent_id = secrets.token_hex(16)
                service = self.runtime.service
                with service.lock:
                    if service.selected_agent is None:
                        service.selected_agent = agent_id
                if kind in ('hello', 'probe', 'status'):
                    self._record_client_status(agent_id, h)
                    ticket, now = self._issue_ticket(agent_id)
                    reply = dict(
                        kind='status_ok',
                        agent_id=agent_id,
                        ticket=ticket,
                        hub_monotonic_ms=now * 1000,
                        hub_state=self.runtime.feedback(),
                    )
                    if h.get('probe'):
                        reply['probe'] = h['probe']
                    ws.send(pack(reply))
                elif kind == 'descriptor':
                    d = validate_descriptor(h.get('descriptor'))
                    j = self._ensure_journal(agent_id, d)
                    descriptor = d
                    if agent_id == service.selected_agent:
                        service.on_descriptor(d)
                    ticket, now = self._issue_ticket(agent_id)
                    ws.send(pack(dict(
                        kind='descriptor_ok',
                        agent_id=agent_id,
                        durable_seq=j.durable_seq,
                        ticket=ticket,
                        hub_monotonic_ms=now * 1000,
                        hub_state=self.runtime.feedback(),
                    )))
                elif kind == 'block':
                    d_in = h.get('descriptor')
                    d = validate_descriptor(d_in) if isinstance(d_in, dict) else descriptor
                    if d is None:
                        raise ValueError('Descriptor must precede samples')
                    descriptor = d
                    b = h.get('block') if isinstance(h.get('block'), dict) else h
                    validate_block(b, body, d)
                    now = time.monotonic()
                    with self.lock:
                        issued = self.tickets.get((agent_id, b.get('ticket')))
                    age = b.get('source_age_ms')
                    source_mark = None
                    if b.get('mode') == 'live' and issued is not None and type(age) in (int, float) and math.isfinite(age) and age >= 0:
                        # The ticket predates this read. Include its full travel
                        # time and never clamp old samples into the live budget.
                        source_mark = issued - float(age) / 1000.0
                    result = service.ingest_external(agent_id, d, b, body, source_mark)
                    if result.get('live_accepted'):
                        with service.lock:
                            service.status['transport'] = 'lan_ws'
                    ticket, now2 = self._issue_ticket(agent_id)
                    ack = dict(
                        kind='ack',
                        ack_seq=b['seq'],
                        durable_seq=result['durable_seq'],
                        duplicate=result['duplicate'],
                        live_accepted=result['live_accepted'],
                        ticket=ticket,
                        hub_monotonic_ms=now2 * 1000,
                    )
                    if b.get('mode') == 'live' or b['seq'] % 16 == 0:
                        ack['hub_state'] = self.runtime.feedback()
                    ws.send(pack(ack))
                elif kind == 'capture_event':
                    ev = h.get('event') if isinstance(h.get('event'), dict) else {}
                    cap_ack = self.runtime.client_capture_event(agent_id, ev)
                    ticket, now = self._issue_ticket(agent_id)
                    ws.send(pack(dict(
                        kind='capture_ack',
                        capture_ack=cap_ack,
                        ticket=ticket,
                        hub_monotonic_ms=now * 1000,
                        hub_state=self.runtime.feedback(),
                    )))
                elif kind == 'close':
                    j = service.journals.get((agent_id, descriptor['stream_id'])) if descriptor else None
                    ws.send(pack(dict(kind='closed', durable_seq=j.durable_seq if j else 0)))
                    break
                else:
                    raise ValueError('Unsupported LAN WebSocket message: ' + str(kind))
        except Exception:
            pass
        finally:
            with self.lock:
                self.active_clients = max(0, self.active_clients - 1)

    def close(self):
        self.stop_event.set()
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except OSError:
                pass
            self.udp_sock = None
        if self.server:
            try:
                self.server.shutdown()
            except Exception:
                pass
            self.server = None
