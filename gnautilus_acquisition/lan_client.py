"""Direct Hotspot / LAN WebSocket client & auto-discovery for EEG acquisition."""
import copy
import bisect
import queue
import ipaddress
import json
import secrets
import socket
import threading
import time
import urllib.request
from websockets.sync.client import connect
from eeg_relay.protocol import encoded, pack, unpack
from .client import Replicator, source_age_ms
from .contract import HEX32
from .feedback import HubFeedbackLog
from .status_link import StatusLink


DEFAULT_LAN_WS_PORT = 8768
LAN_WS_PATH = '/eeg/v1/lan'


def parse_lan_target(host_hint='', default_port=DEFAULT_LAN_WS_PORT):
    raw = str(host_hint or '').strip()
    if not raw:
        return None, int(default_port)
    for prefix in ('ws://', 'wss://', 'http://', 'https://'):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.split('/', 1)[0].strip()
    if ':' in raw:
        host_part, port_part = raw.rsplit(':', 1)
        try:
            return host_part.strip() or None, int(port_part.strip())
        except ValueError:
            return raw, int(default_port)
    return raw, int(default_port)


def _local_subnet_candidates():
    hosts = ['192.168.137.1', '127.0.0.1']
    broadcasts = ['255.255.255.255', '192.168.137.255']
    seen_hosts = set(hosts)
    seen_bcast = set(broadcasts)
    addrs = []
    try:
        import psutil
        for iface_addrs in psutil.net_if_addrs().values():
            for snic in iface_addrs:
                if getattr(snic, 'family', None) == socket.AF_INET and snic.address:
                    addrs.append(snic.address)
    except Exception:
        pass
    try:
        _, _, ip_list = socket.gethostbyname_ex(socket.gethostname())
        addrs.extend(ip_list)
    except Exception:
        pass
    for addr in addrs:
        try:
            ip = ipaddress.IPv4Address(addr)
            if ip.is_loopback or ip.is_unspecified:
                continue
            parts = addr.split('.')
            if len(parts) == 4:
                gw = f'{parts[0]}.{parts[1]}.{parts[2]}.1'
                bc = f'{parts[0]}.{parts[1]}.{parts[2]}.255'
                if gw not in seen_hosts:
                    seen_hosts.add(gw)
                    hosts.insert(1, gw)
                if bc not in seen_bcast:
                    seen_bcast.add(bc)
                    broadcasts.append(bc)
        except Exception:
            pass
    return hosts, broadcasts


def _http_probe_lan(host, port, timeout=0.35):
    url = f'http://{host}:{port}/eeg/v1/lan/info'
    req = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8', errors='replace'))
        if isinstance(data, dict) and data.get('service') == 'eegmovement_hub_v1':
            return dict(host=host, port=int(data.get('port') or port), ws_path=data.get('ws_path') or LAN_WS_PATH)
    return None


def discover_lan_hub(host_hint='', default_port=DEFAULT_LAN_WS_PORT, timeout=0.6):
    explicit_host, port = parse_lan_target(host_hint, default_port)
    if explicit_host:
        try:
            found = _http_probe_lan(explicit_host, port, timeout=min(0.6, timeout))
            if found:
                return found
        except Exception:
            pass
    hosts, broadcasts = _local_subnet_candidates()
    if explicit_host and explicit_host not in hosts:
        hosts.insert(0, explicit_host)
    udp_targets = [(b, port) for b in broadcasts] + [(h, port) for h in hosts]
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(min(0.25, max(0.1, timeout / 2)))
        payload = encoded(dict(discover='eegmovement_hub_v1'))
        for target in udp_targets:
            try:
                sock.sendto(payload, target)
            except OSError:
                pass
        deadline = time.monotonic() + min(0.3, max(0.12, timeout / 2))
        while time.monotonic() < deadline:
            try:
                raw, addr = sock.recvfrom(2048)
                msg = json.loads(raw.decode('utf-8', errors='replace'))
                if isinstance(msg, dict) and msg.get('service') == 'eegmovement_hub_v1':
                    sock.close()
                    return dict(host=addr[0], port=int(msg.get('port') or port), ws_path=msg.get('ws_path') or LAN_WS_PATH)
            except socket.timeout:
                break
            except Exception:
                pass
        sock.close()
    except Exception:
        pass
    for candidate_host in hosts[:6]:
        try:
            found = _http_probe_lan(candidate_host, port, timeout=0.25)
            if found:
                return found
        except Exception:
            pass
    return None


def build_lan_enrollment(host_hint='', agent_id=None, default_port=DEFAULT_LAN_WS_PORT):
    agent = str(agent_id or '').lower()
    if not HEX32.fullmatch(agent):
        agent = secrets.token_hex(16)
    explicit_host, port = parse_lan_target(host_hint, default_port)
    found = discover_lan_hub(host_hint, port, timeout=0.5)
    if found:
        host = found['host']
        port = found['port']
        ws_path = found.get('ws_path') or LAN_WS_PATH
    elif explicit_host:
        host = explicit_host
        ws_path = LAN_WS_PATH
    else:
        host = '192.168.137.1'
        ws_path = LAN_WS_PATH
    return dict(
        schema='gnautilus.enrollment.lan.v1',
        mode='lan_ws',
        host=host,
        port=int(port),
        url=f'ws://{host}:{port}{ws_path}',
        agent_id=agent,
        device_id=agent,
        host_hint=str(host_hint or '').strip(),
        discovered=found is not None,
    )


def probe_lan(enrollment, on_event=None):
    callback = on_event or (lambda *args: None)
    began = time.monotonic()
    target_hint = enrollment.get('host_hint') or f"{enrollment.get('host', '192.168.137.1')}:{enrollment.get('port', DEFAULT_LAN_WS_PORT)}"
    found = discover_lan_hub(target_hint, enrollment.get('port', DEFAULT_LAN_WS_PORT), timeout=0.6)
    if found:
        enrollment['host'] = found['host']
        enrollment['port'] = found['port']
        enrollment['url'] = f"ws://{found['host']}:{found['port']}{found.get('ws_path') or LAN_WS_PATH}"
    url = enrollment.get('url') or f"ws://{enrollment['host']}:{enrollment['port']}{LAN_WS_PATH}"
    callback('热点直连', 'INFO', f'正在连接中枢热点 WebSocket {url}')
    ws = connect(url, open_timeout=2.5, close_timeout=1, max_size=8 * 1024 * 1024, compression=None)
    try:
        nonce = secrets.token_hex(8)
        ws.send(pack(dict(kind='probe', agent_id=enrollment['agent_id'], probe=nonce, sdk_state='diagnostic', disk_state='not_started')))
        raw = ws.recv(timeout=2.5)
        reply, _ = unpack(raw) if isinstance(raw, bytes) else (json.loads(raw), b'')
        if reply.get('kind') != 'status_ok':
            raise ValueError('中枢热点 WebSocket 响应格式不匹配')
        elapsed = round((time.monotonic() - began) * 1000, 1)
        callback('热点直连', 'INFO', f"中枢热点 WebSocket 直连已确认 ({enrollment['host']}:{enrollment['port']} · {elapsed}ms)")
        return dict(
            connected=True,
            transport='lan_ws',
            path=f"热点/局域网 WebSocket ({enrollment['host']}:{enrollment['port']})",
            elapsed_ms=elapsed,
            hub=reply.get('hub_state', {}),
        )
    finally:
        try:
            ws.close()
        except Exception:
            pass


class LanStatusLink(StatusLink):
    def __init__(self, enrollment, on_event=None):
        self.config = dict(enrollment)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.on_event = on_event or (lambda *a: None)
        self.last_hub = 0.0
        self.hub = {}
        self.health = {}
        self.feedback_log = HubFeedbackLog()
        self.thread = None
        self.state = dict(
            connected=False,
            transport='lan_ws',
            path=f"热点/局域网 WebSocket ({self.config.get('host', '192.168.137.1')}:{self.config.get('port', DEFAULT_LAN_WS_PORT)})",
            status_only=True,
            error=None,
        )

    def start(self):
        with self.lock:
            if self.thread:
                return self
            self.thread = threading.Thread(target=self.run, daemon=True, name='EEG LAN idle status link')
            self.thread.start()
        return self

    def update_health(self, health):
        with self.lock:
            self.health = copy.deepcopy(health)

    def _resolve_url(self):
        hint = self.config.get('host_hint') or ''
        found = discover_lan_hub(hint or f"{self.config.get('host', '192.168.137.1')}:{self.config.get('port', DEFAULT_LAN_WS_PORT)}", self.config.get('port', DEFAULT_LAN_WS_PORT), timeout=0.4)
        if found:
            self.config['host'] = found['host']
            self.config['port'] = found['port']
            self.config['url'] = f"ws://{found['host']}:{found['port']}{found.get('ws_path') or LAN_WS_PATH}"
        return self.config.get('url') or f"ws://{self.config.get('host', '192.168.137.1')}:{self.config.get('port', DEFAULT_LAN_WS_PORT)}{LAN_WS_PATH}"

    def run(self):
        while not self.stop_event.is_set():
            ws = None
            try:
                url = self._resolve_url()
                ws = connect(url, open_timeout=2.0, close_timeout=1, max_size=8 * 1024 * 1024, compression=None)
                with self.lock:
                    self.state.update(
                        connected=True,
                        error=None,
                        path=f"热点/局域网 WebSocket ({self.config.get('host')}:{self.config.get('port')})",
                    )
                self.on_event('热点直连', 'INFO', f"已连接中枢热点 WebSocket：{self.config.get('host')}:{self.config.get('port')}")
                while not self.stop_event.is_set():
                    with self.lock:
                        health = copy.deepcopy(self.health)
                    health.setdefault('sdk_state', 'not_started')
                    health.setdefault('disk_state', 'not_recording')
                    ws.send(pack(dict(health, kind='status', agent_id=self.config['agent_id'])))
                    raw = ws.recv(timeout=2.0)
                    reply, _ = unpack(raw) if isinstance(raw, bytes) else (json.loads(raw), b'')
                    if reply.get('kind') == 'status_ok' and isinstance(reply.get('hub_state'), dict):
                        self.feedback_log.consume(reply['hub_state'], self.on_event)
                        with self.lock:
                            self.hub = reply['hub_state']
                            self.last_hub = time.monotonic()
                            self.state['error'] = None
                    if self.stop_event.wait(0.4):
                        break
            except Exception as error:
                with self.lock:
                    self.state.update(connected=False, error=f'{type(error).__name__}: {error}'[:160])
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
            self.stop_event.wait(0.8)

    def probe(self, on_event=None, timeout=5):
        self.start()
        result = probe_lan(self.config, on_event=on_event)
        with self.lock:
            self.hub = result.get('hub', {})
            self.last_hub = time.monotonic()
            self.state.update(connected=True, error=None, path=result.get('path'))
        return result

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
            age = (time.monotonic() - self.last_hub) * 1000 if self.last_hub else None
            fresh = bool(result['connected'] and age is not None and age < 1500)
            result.update(hub_online=fresh, hub_state_fresh=fresh, hub_age_ms=age, hub=copy.deepcopy(self.hub) if fresh else {})
            return result

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(3)


class LanWsReplicator(Replicator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ticket = None
        self.last_hub = 0.0
        self.state.update(
            transport='lan_ws',
            cloud_blocks=0,
            hub_online=False,
            path=f"热点/局域网 WebSocket ({self.enrollment.get('host', '192.168.137.1')}:{self.enrollment.get('port', DEFAULT_LAN_WS_PORT)})",
        )

    def _resolve_url(self):
        hint = self.enrollment.get('host_hint') or ''
        target = hint or f"{self.enrollment.get('host', '192.168.137.1')}:{self.enrollment.get('port', DEFAULT_LAN_WS_PORT)}"
        found = discover_lan_hub(target, self.enrollment.get('port', DEFAULT_LAN_WS_PORT), timeout=0.4)
        if found:
            self.enrollment['host'] = found['host']
            self.enrollment['port'] = found['port']
            self.enrollment['url'] = f"ws://{found['host']}:{found['port']}{found.get('ws_path') or LAN_WS_PATH}"
        return self.enrollment.get('url') or f"ws://{self.enrollment.get('host', '192.168.137.1')}:{self.enrollment.get('port', DEFAULT_LAN_WS_PORT)}{LAN_WS_PATH}"

    def _next_block(self):
        """Reserve oldest unsent fresh data, with a bounded acknowledgement window."""
        with self.lock:
            if len(self.pending) >= 8 or not self.ticket:
                return None
            durable = self.state['durable_seq']
            now_ns = time.monotonic_ns()
            with self.journal.lock:
                numbers = self.journal.sequences
                at = bisect.bisect_right(numbers, max(durable, self.live_scan_seq))
                if not self.backfill_only:
                    for seq in numbers[at:at + 64]:
                        self.live_scan_seq = seq
                        hdr = self.journal.records[seq]['header']
                        if seq in self.pending or seq in self.acked:
                            continue
                        if source_age_ms(hdr, self.journal.descriptor['sampling_rate'], now_ns) < 180:
                            header, body = self.journal.read(seq)
                            self.pending[seq] = time.monotonic()
                            self.last_live_seq = seq
                            return header, body, 'live'
                    # Continue a bounded scan without sending history ahead of
                    # fresh candidates beyond this slice.
                    if at + 64 < len(numbers):
                        return None
                # At most two history blocks may be in flight. They never
                # consume the six slots reserved for fresh EEG.
                if len(self.pending) >= 2:
                    return None
                at = bisect.bisect_right(numbers, durable)
                for seq in numbers[at:at + 64]:
                    hdr = self.journal.records[seq]['header']
                    if seq in self.pending or seq in self.acked:
                        continue
                    if not self.backfill_only and source_age_ms(hdr, self.journal.descriptor['sampling_rate'], now_ns) < 180:
                        continue
                    header, body = self.journal.read(seq)
                    self.pending[seq] = time.monotonic()
                    return header, body, 'backfill'
        return None

    def _receive(self, ws, broken, errors):
        try:
            while not self.stop_event.is_set() and not broken.is_set():
                try:
                    raw = ws.recv(timeout=.2)
                except TimeoutError:
                    continue
                reply, _ = unpack(raw) if isinstance(raw, bytes) else (json.loads(raw), b'')
                kind = reply.get('kind')
                if kind not in ('ack', 'status_ok', 'capture_ack', 'closed'):
                    raise ValueError('Unsupported LAN WebSocket acknowledgement')
                with self.lock:
                    if kind == 'ack':
                        seq = reply.get('ack_seq')
                        if seq not in self.pending:
                            raise ValueError('Mismatched LAN WebSocket block acknowledgement')
                        sent_at = self.pending.pop(seq)
                        self.acked.add(seq)
                        self.state['durable_seq'] = max(self.state['durable_seq'], reply['durable_seq'])
                        self.acked = {n for n in self.acked if n > self.state['durable_seq']}
                        self.state['cloud_blocks'] = self.state['durable_seq']
                        self.state['live_accepted'] += bool(reply.get('live_accepted'))
                        self.state['ack_rtt_ms'] = round((time.monotonic() - sent_at) * 1000, 2)
                        self.state['inflight_blocks'] = len(self.pending)
                    self.ticket = reply.get('ticket') or self.ticket
                    self.last_hub = time.monotonic()
                    self.state['hub_online'] = True
                self._feedback(reply)
        except Exception as error:
            if not self.stop_event.is_set():
                errors.append(error)
            broken.set()

    def _run(self):
        while not self.stop_event.is_set():
            ws = None
            reader = None
            broken = threading.Event()
            errors = []
            try:
                url = self._resolve_url()
                ws = connect(url, open_timeout=2.5, close_timeout=1, max_size=8 * 1024 * 1024, compression=None)
                self.socket = getattr(ws, 'socket', None)
                if self.socket is not None:
                    self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.on_event('热点直连', 'INFO', f'已连接中枢热点 WebSocket ({url})，正在同步流描述')
                ws.send(pack(dict(kind='descriptor', agent_id=self.enrollment['agent_id'], descriptor=self.journal.descriptor)))
                raw = ws.recv(timeout=3)
                reply, _ = unpack(raw) if isinstance(raw, bytes) else (json.loads(raw), b'')
                if reply.get('kind') != 'descriptor_ok':
                    raise ValueError('中枢未接受流描述')
                with self.lock:
                    self.ticket = reply.get('ticket')
                    self.last_hub = time.monotonic()
                    self.pending = {}
                    self.acked.clear()
                    self.live_scan_seq = self.last_live_seq = reply['durable_seq']
                    self.state.update(connected=True, hub_online=True, durable_seq=reply['durable_seq'],
                                      cloud_blocks=reply['durable_seq'], error=None, inflight_blocks=0)
                self._feedback(reply)
                reader = threading.Thread(target=self._receive, args=(ws, broken, errors), daemon=True, name='LAN acknowledgements')
                reader.start()
                last_health = last_telemetry = 0.
                while not self.stop_event.is_set() and not broken.is_set():
                    now = time.monotonic()
                    with self.lock:
                        if any(now - started > 3 for started in self.pending.values()):
                            raise TimeoutError('热点数据确认超时；保留本地记录后重连')
                        self.state['hub_online'] = now - self.last_hub < 1.5
                    # Control messages share the ordered socket but never wait
                    # for an individual block round trip on the sending thread.
                    for _ in range(8):
                        try:
                            ev = self.capture_events.get_nowait()
                        except queue.Empty:
                            break
                        ws.send(pack(dict(kind='capture_event', agent_id=self.enrollment['agent_id'], event=ev)))
                    if now - last_health >= .1:
                        detailed = self.health_provider is not None and now - last_telemetry >= .5
                        health = self.health_provider() if detailed else dict(
                            sdk_state='backfill' if self.backfill_only else 'receiving', disk_state='durable')
                        if detailed:
                            last_telemetry = now
                        ws.send(pack(dict(health, kind='status', agent_id=self.enrollment['agent_id'], stream_id=self.journal.descriptor['stream_id'])))
                        last_health = now
                    item = self._next_block()
                    if item is not None:
                        header, body, mode = item
                        age = source_age_ms(header, self.journal.descriptor['sampling_rate'])
                        if mode == 'live' and age >= 180:
                            with self.lock:
                                self.pending.pop(header['seq'], None)
                            continue
                        block = dict(header, mode=mode, ticket=self.ticket, source_age_ms=age)
                        ws.send(pack(dict(kind='block', agent_id=self.enrollment['agent_id'], block=block), body))
                        with self.lock:
                            self.state['sent_blocks'] += 1
                            self.state['inflight_blocks'] = len(self.pending)
                        continue
                    self.stop_event.wait(.002)
                if errors:
                    raise errors[0]
            except Exception as error:
                with self.lock:
                    self.state.update(connected=False, hub_online=False, error=f'{type(error).__name__}: {error}'[:200])
                if not self.stop_event.is_set():
                    self.on_event('热点直连', 'WARN', str(error)[:160] + '；本地记录保留，正在重连')
            finally:
                broken.set()
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
                if reader is not None:
                    reader.join(3)
                    if reader.is_alive():
                        self.state['error'] = '热点确认线程未退出，停止重连并保留本地记录'
                        self.stop_event.set()
                self.socket = None
            self.stop_event.wait(.5)
        with self.lock:
            self.state.update(connected=False, hub_online=False)
