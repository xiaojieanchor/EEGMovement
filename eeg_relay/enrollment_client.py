"""Automatic EEG role enrollment. Private material stays in the local app data."""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .network import paths
from .update import opener

LIMIT = 16384


class EnrollmentError(ConnectionError):
    def __init__(self, state, message):
        self.state = state
        super().__init__(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    handler_order = 100
    def redirect_request(self, request, response, code, message, headers, newurl):
        # The request header contains the pending token. Never forward it.
        raise ValueError('自动连接服务不允许重定向')


def _private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


class EnrollmentClient:
    """Blocking, bounded worker API; the UI owns the thread and cancel event."""
    def __init__(self, config, identity_dir):
        self.config = dict(config)
        base = urlparse(self.config.get('base_url', ''))
        if (base.scheme != 'https' or not base.hostname or base.username or base.password
                or base.query or base.fragment or not base.path.endswith('/')
                or not isinstance(self.config.get('ca_pem'), str) or not self.config['ca_pem']):
            raise ValueError('自动连接服务必须使用应用专用CA验证的HTTPS地址')
        self.identity_path = Path(identity_dir)/'relay-identity.json'
        self.pending_path = Path(identity_dir)/'enrollment-pending.json'

    def _validate_identity(self, value):
        if not isinstance(value, dict) or value.get('schema') != 'gnautilus.enrollment.v2' or value.get('mode') != 'relay':
            raise ValueError('中枢返回的采集身份无效')
        if value.get('role', 'eeg') != 'eeg':raise ValueError('中枢返回的设备类型不匹配')
        for field, length in [('device_id',32), ('hub_id',32), ('workspace',32), ('secret',64), ('auth_secret',64)]:
            if not re.fullmatch('[0-9a-f]{'+str(length)+'}', str(value.get(field,''))):
                raise ValueError('中枢返回的采集身份不完整')
        base = urlparse(self.config['base_url'])
        expected = 'wss://'+base.netloc+base.path+'v1/ws'
        target = urlparse(value.get('url',''))
        # Explicit/default 443 represent the same endpoint.
        if (target.scheme != 'wss' or target.hostname != base.hostname or
                (target.port or 443) != (base.port or 443) or target.path != urlparse(expected).path or
                target.query or target.fragment or target.username or target.password or
                value.get('ca_pem') != self.config['ca_pem']):
            raise ValueError('中枢返回的公网地址或应用CA不匹配')
        return value

    def _read_json(self, path):
        with path.open('rb') as source:raw = source.read(LIMIT+1)
        if len(raw)>LIMIT:raise ValueError('本地连接身份文件过大')
        return json.loads(raw.decode('utf-8'))

    def _pending(self):
        if self.pending_path.exists():
            try:
                value = self._read_json(self.pending_path)
                if (isinstance(value,dict) and re.fullmatch('[0-9a-f]{32}',value.get('request_id',''))
                        and re.fullmatch('[0-9a-f]{64}',value.get('token',''))
                        and type(value.get('created')) in (int,float) and 0 <= time.time()-value['created'] < 600):
                    key = serialization.load_pem_private_key(value['private_key'].encode(), password=None)
                    if isinstance(key,rsa.RSAPrivateKey) and key.key_size==3072:return value,key
            except (OSError,ValueError,TypeError,KeyError):pass
        key = rsa.generate_private_key(public_exponent=65537,key_size=3072)
        value = dict(request_id=secrets.token_hex(16), token=secrets.token_hex(32), created=time.time(),
                     private_key=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()).decode())
        _private_json(self.pending_path,value)
        return value,key

    @staticmethod
    def _check(stop, deadline):
        if stop.is_set():raise InterruptedError('自动连接已取消；可点击“连接中枢”继续')
        if time.monotonic()>=deadline:raise TimeoutError('自动连接等待已超过10分钟，请确认中枢在线后重新连接')

    def _request(self, endpoint, payload, stop, deadline):
        encoded = base64.b64encode(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()).decode()
        request = urllib.request.Request(self.config['base_url']+'v1/enroll/'+endpoint,
            headers={'X-EEG-Request':encoded, 'Accept':'application/json'}, method='GET')
        errors = []
        for _,proxy in paths(self.config):
            self._check(stop,deadline)
            transport = opener(self.config,proxy)
            transport.add_handler(_NoRedirect())
            try:
                with transport.open(request,timeout=min(8,max(.001,deadline-time.monotonic()))) as response:
                    if response.geturl() != request.full_url:raise ValueError('自动连接服务地址发生重定向')
                    raw = response.read(LIMIT+1)
                self._check(stop,deadline)
                if len(raw)>LIMIT:raise ValueError('自动连接服务响应过大')
                value = json.loads(raw.decode('utf-8'))
                if not isinstance(value,dict):raise ValueError('自动连接服务响应无效')
                return value
            except urllib.error.HTTPError as error:
                code=error.code
                error.close()
                if code==409:raise EnrollmentError('role_occupied','已有采集端在线，请先结束该采集端连接后重试') from None
                if code in (404,410):
                    self.pending_path.unlink(missing_ok=True)
                    raise EnrollmentError('expired','自动连接请求已过期，请重新连接') from None
                errors.append('HTTP '+str(code))
            except InterruptedError:raise
            except TimeoutError:
                self._check(stop,deadline)
                errors.append('TimeoutError')
            except ValueError:raise
            except Exception as error:
                # Exception text may contain request headers; emit only its type.
                errors.append(type(error).__name__)
        self._check(stop,deadline)
        raise ConnectionError('自动连接服务暂不可达：'+'/'.join(errors))

    def _decrypt(self, response, pending, key):
        try:
            sealed=response['sealed']
            wrapped=base64.b64decode(sealed['key'],validate=True)
            nonce=base64.b64decode(sealed['nonce'],validate=True)
            ciphertext=base64.b64decode(sealed['ciphertext'],validate=True)
            if len(wrapped)!=384 or len(nonce)!=12:raise ValueError('Invalid sealed dimensions')
            aes=key.decrypt(wrapped,padding.OAEP(mgf=padding.MGF1(hashes.SHA1()),algorithm=hashes.SHA256(),label=None))
            if len(aes)!=32:raise ValueError('Invalid AES key')
            raw=AESGCM(aes).decrypt(nonce,ciphertext,pending['request_id'].encode())
            return self._validate_identity(json.loads(raw.decode('utf-8')))
        except Exception:
            raise ValueError('中枢返回的采集身份未通过解密与完整性核对') from None

    def run(self, *, stop_event=None, on_state=None, timeout=600, poll_interval=2):
        stop=stop_event if stop_event is not None else threading.Event()
        notify=on_state or (lambda state:None)
        deadline=time.monotonic()+min(600,max(0,float(timeout)))
        self._check(stop,deadline)
        if self.identity_path.exists():
            value=self._validate_identity(self._read_json(self.identity_path))
            notify(dict(state='ready'))
            return value
        pending,key=self._pending()
        deadline=min(deadline,time.monotonic()+max(0,600-(time.time()-pending['created'])))
        self._check(stop,deadline)
        notify(dict(state='connecting'))
        payload=dict(request_id=pending['request_id'],token=pending['token'],role='eeg',name=socket.gethostname()[:128],
                     public_key=key.public_key().public_bytes(serialization.Encoding.PEM,
                                                          serialization.PublicFormat.SubjectPublicKeyInfo).decode())
        response=self._request('start',payload,stop,deadline)
        while True:
            self._check(stop,deadline)
            if response.get('request_id',pending['request_id']) != pending['request_id']:
                raise ValueError('自动连接请求身份不匹配')
            state=response.get('state')
            if state=='ready':
                identity=self._decrypt(response,pending,key)
                self._check(stop,deadline)
                _private_json(self.identity_path,identity)
                self.pending_path.unlink(missing_ok=True)
                notify(dict(state='ready'))
                return identity
            if state=='role_occupied':raise EnrollmentError(state,'已有采集端在线，请先结束该采集端连接后重试')
            if state!='waiting_hub':raise ValueError('自动连接服务返回未知状态')
            notify(dict(state='waiting_hub'))
            if stop.wait(min(max(.001,poll_interval),max(0,deadline-time.monotonic()))):self._check(stop,deadline)
            self._check(stop,deadline)
            response=self._request('poll',dict(request_id=pending['request_id'],token=pending['token']),stop,deadline)
