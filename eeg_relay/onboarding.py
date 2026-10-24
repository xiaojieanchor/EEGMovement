"""Automatic single-role onboarding. Cloud sees only client public keys and sealed identities."""
import base64,hashlib,hmac,json,os,re,secrets,sqlite3,threading,time,urllib.request
from pathlib import Path
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def seal_identity(identity,public_key,request_id):
    public=serialization.load_pem_public_key(public_key.encode())
    if not isinstance(public,rsa.RSAPublicKey) or not 3072<=public.key_size<=4096:raise ValueError('Invalid enrollment public key')
    key=secrets.token_bytes(32);nonce=secrets.token_bytes(12)
    wrapped=public.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA1()),algorithm=hashes.SHA256(),label=None))
    cipher=AESGCM(key).encrypt(nonce,json.dumps(identity,separators=(',',':')).encode(),request_id.encode())
    return {k:base64.b64encode(v).decode() for k,v in dict(key=wrapped,nonce=nonce,ciphertext=cipher).items()}

class EnrollmentBroker:
    def __init__(self,root,occupied):
        self.occupied=occupied;self.lock=threading.RLock();self.nonces={};self.rate={}
        self.db=sqlite3.connect(Path(root)/'onboarding.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,token TEXT,role TEXT,public_key TEXT,name TEXT,created REAL,sealed TEXT)');self.db.commit()
    def expire(self):
        self.db.execute('DELETE FROM requests WHERE created<?',(time.time()-600,));self.db.commit()
    def start(self,data):
        identity,token,role=data.get('request_id',''),data.get('token',''),data.get('role');public=data.get('public_key','')
        if not re.fullmatch('[0-9a-f]{32}',str(identity)) or not re.fullmatch('[0-9a-f]{64}',str(token)) or role not in ('eeg','quest') or not isinstance(public,str) or len(public)>2048:raise ValueError('Invalid enrollment request')
        digest=hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            self.expire();previous=self.db.execute('SELECT token,role,public_key FROM requests WHERE id=?',(identity,)).fetchone()
            if previous:
                if previous!=(digest,role,public):raise ValueError('identity_conflict')
                return self.poll(data)
            if self.occupied(role):raise ValueError('role_occupied')
            if self.db.execute('SELECT count(*) FROM requests WHERE role=? AND sealed IS NULL',(role,)).fetchone()[0]:raise ValueError('role_occupied: registration already pending')
            parsed=serialization.load_pem_public_key(public.encode())
            if not isinstance(parsed,rsa.RSAPublicKey) or not 3072<=parsed.key_size<=4096:raise ValueError('Invalid enrollment public key')
            name=str(data.get('name',''))[:80]
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,NULL)',(identity,digest,role,public,name,time.time()));self.db.commit()
            return dict(state='waiting_hub',request_id=identity)
    def poll(self,data):
        with self.lock:
            row=self.db.execute('SELECT token,sealed,created FROM requests WHERE id=?',(str(data.get('request_id','')),)).fetchone()
            if not row or not hmac.compare_digest(row[0],hashlib.sha256(str(data.get('token','')).encode()).hexdigest()):raise ValueError('request_rejected')
            if time.time()-row[2]>600:raise ValueError('request_expired')
            return dict(state='ready',sealed=json.loads(row[1])) if row[1] else dict(state='waiting_hub',request_id=data['request_id'])
    def pending(self):
        with self.lock:
            self.expire()
            rows=self.db.execute('SELECT id,role,public_key,name FROM requests WHERE sealed IS NULL ORDER BY created').fetchall()
            return [dict(request_id=i,role=r,public_key=p,name=n,occupied=self.occupied(r)) for i,r,p,n in rows]
    def complete(self,identity,role,sealed):
        if role not in ('eeg','quest') or not isinstance(sealed,dict) or set(sealed)!=set(('key','nonce','ciphertext')):raise ValueError('Invalid sealed identity')
        if any(not isinstance(v,str) for v in sealed.values()) or len(json.dumps(sealed))>12000:raise ValueError('Invalid sealed size')
        with self.lock:
            self.expire();row=self.db.execute('SELECT role,sealed FROM requests WHERE id=?',(identity,)).fetchone()
            if not row or row[0]!=role:raise ValueError('request_expired')
            if self.occupied(role):raise ValueError('role_occupied')
            raw=json.dumps(sealed,sort_keys=True)
            if row[1] and row[1]!=raw:raise ValueError('Changed sealed retry')
            self.db.execute('UPDATE requests SET sealed=? WHERE id=?',(raw,identity));self.db.commit()
    def authenticate(self,path,headers,records):
        identity=headers.get('X-EEG-Hub','');record=records.get(identity)
        if not record or record['role']!='hub':raise ValueError('Hub authentication required')
        timestamp,nonce=headers.get('X-EEG-Time',''),headers.get('X-EEG-Nonce','');raw=headers.get('X-EEG-Request','')
        try:valid_time=abs(time.time()-int(timestamp))<30
        except (ValueError,TypeError):valid_time=False
        if not valid_time or not re.fullmatch('[0-9a-f]{32}',nonce):raise ValueError('Invalid Hub request time')
        message='\n'.join(('GET',path,identity,timestamp,nonce,hashlib.sha256(raw.encode()).hexdigest())).encode()
        expected=hmac.new(bytes.fromhex(record['auth_secret']),message,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(headers.get('X-EEG-MAC',''),expected):raise ValueError('Invalid Hub request signature')
        with self.lock:
            self.nonces={k:v for k,v in self.nonces.items() if time.monotonic()-v<60}
            if nonce in self.nonces or len(self.nonces)>=1024:raise ValueError('Hub request replay')
            self.nonces[nonce]=time.monotonic()
        return identity,record
    def close(self):
        with self.lock:self.db.close()

def hub_call(config,operation,data=None):
    from .network import paths
    from .update import opener
    from urllib.parse import urlparse
    url=urlparse(config['url']);base='https://'+url.netloc+'/eeg/';config=dict(config,base_url=base)
    path='/eeg/v1/enroll/'+operation;raw=base64.b64encode(json.dumps(data or {},separators=(',',':')).encode()).decode()
    stamp,nonce=str(int(time.time())),secrets.token_hex(16)
    message='\n'.join(('GET',path,config['device_id'],stamp,nonce,hashlib.sha256(raw.encode()).hexdigest())).encode()
    headers={'X-EEG-Request':raw,'X-EEG-Hub':config['device_id'],'X-EEG-Time':stamp,'X-EEG-Nonce':nonce,'X-EEG-MAC':hmac.new(bytes.fromhex(config['auth_secret']),message,hashlib.sha256).hexdigest()}
    errors=[]
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        handler_order=100
        def redirect_request(self,*args,**kwargs):raise ValueError('Automatic role service cannot redirect')
    for _,proxy in paths(config):
        try:
            request=urllib.request.Request('https://'+url.netloc+path,headers=headers)
            transport=opener(config,proxy);transport.add_handler(NoRedirect())
            with transport.open(request,timeout=5) as reply:
                if reply.geturl()!=request.full_url:raise ValueError('Automatic role service origin changed')
                value=reply.read(16385)
                if len(value)>16384:raise ValueError('Oversized enrollment response')
                return json.loads(value)
        except Exception as error:errors.append(type(error).__name__)
    raise ConnectionError('自动连接服务未确认：'+'/'.join(errors))
