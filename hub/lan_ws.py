"""Direct Hotspot / LAN WebSocket ingest & discovery for EEG acquisition client."""
import datetime
import collections
import json
import math
import os
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


def lan_source_mark(issued, now, age, elapsed=None):
    if issued is None or type(age) not in (int,float) or not math.isfinite(age) or age < 0 or issued > now:
        return None
    if elapsed is None:
        return issued-age/1000
    # Receipt of a ticket is later than its Hub issuance. Adding only the
    # client's elapsed interval yields a lower bound without synchronized clocks.
    # Bound impossible/future claims and subtract a 2ms short-interval drift margin.
    if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or not 0 <= elapsed <= 2000 or elapsed > (now-issued)*1000+2:
        return None
    return min(issued+elapsed/1000-age/1000-.002, now-age/1000)


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
        self.error = None
        self.discovery_error = None
        self.timing = {}
        self.failures = collections.deque(maxlen=8)
        self.block_stats = collections.Counter()

    def start(self):
        if self.requested_port is None or self.requested_port < 0:
            return self
        self._bind_once()
        self.listener_thread = threading.Thread(target=self._retry_listener, daemon=True, name='Hub LAN listener recovery')
        self.listener_thread.start()
        return self

    def _retry_listener(self):
        while not self.stop_event.wait(.5):
            if self.server is None:
                self._bind_once()

    def _bind_once(self):
        with self.lock:
            if self.stop_event.is_set() or self.server is not None:
                return
            try:
                self.server = serve(
                    self._handle_ws,
                    self.host,
                    self.requested_port,
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
                self.error = None
            except OSError as error:
                self.server = None
                self.error = f'热点端口 {self.requested_port} 监听失败：{error}；保留固定端口重试，不切换随机端口'
        if self.server is None:
            return
        threading.Thread(target=self.server.serve_forever, daemon=True, name='Hub LAN WebSocket').start()
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind((self.host, self.port))
            udp.settimeout(0.3)
            self.udp_sock = udp
            threading.Thread(target=self._serve_udp, daemon=True, name='Hub LAN UDP discovery').start()
        except OSError as error:
            self.udp_sock = None
            self.discovery_error = str(error)[:240]

    def snapshot(self):
        with self.lock:
            return dict(requested_port=self.requested_port, port=self.port, listening=self.server is not None,
                        discovery_ready=self.udp_sock is not None, error=self.error,
                        discovery_error=self.discovery_error, active_clients=self.active_clients, timing=dict(self.timing),
                        failures=list(self.failures),block_stats=dict(self.block_stats))

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
                hub_pid=os.getpid(),
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
            # Descriptor handshakes also come from historical recovery jobs.
            # Only an accepted live block may replace the runtime descriptor.
            if self.runtime.descriptor is None:
                service.status.update(enabled=True, connected=True, state='waiting_samples', agent_id=agent_id, transport='lan_ws')
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
        kind = 'opening'
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
                    source_mark = lan_source_mark(issued, now, age, b.get('ticket_elapsed_ms')) if b.get('mode')=='live' else None
                    ingest_start = time.monotonic()
                    result = service.ingest_external(agent_id, d, b, body, source_mark, transport='lan_ws')
                    self.timing = dict(ingest_ms=round((time.monotonic()-ingest_start)*1000,2),
                                       client_age_ms=age, ticket_elapsed_ms=b.get('ticket_elapsed_ms'),
                                       ticket_age_ms=round((now-issued)*1000,2) if issued is not None else None,
                                       block_seq=b['seq'],mode=b['mode'],live_accepted=result.get('live_accepted'),
                                       bound_age_ms=round((now-source_mark)*1000,2) if source_mark is not None else None)
                    with self.lock:
                        self.block_stats['blocks']+=1
                        self.block_stats['live_accepted']+=bool(result.get('live_accepted'))
                        self.block_stats['duplicate']+=bool(result.get('duplicate'))
                        if b['mode']=='live' and not result.get('live_accepted'):
                            self.block_stats['live_rejected']+=1
                            self.block_stats['missing_or_invalid_ticket']+=source_mark is None
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
        except Exception as error:
            with self.lock:
                self.failures.append(dict(time_ns=time.time_ns(),kind=kind,error=type(error).__name__+': '+str(error)[:240]))
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
        if hasattr(self, 'listener_thread') and self.listener_thread is not threading.current_thread():
            self.listener_thread.join(2)
