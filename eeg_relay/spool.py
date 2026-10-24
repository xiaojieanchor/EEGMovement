"""SQLite FULL/WAL commits with AES-GCM payloads; cloud receipt != Hub receipt."""
import hashlib,os,shutil,sqlite3,threading,time
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .protocol import pack,unpack,stable_digest,encoded

class CapacityError(RuntimeError):pass

class Spool:
    def __init__(self,root,key,*,limit=8*1024**3,reserve=10*1024**3,ttl=86400):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        if len(key)!=32:raise ValueError('Invalid cache key')
        self.aes=AESGCM(key);self.limit,self.reserve,self.ttl=limit,reserve,ttl;self.lock=threading.RLock();self.last_error=None
        self.db=sqlite3.connect(self.root/'cache.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS cache(id TEXT PRIMARY KEY,workspace TEXT,source TEXT,target TEXT,stream TEXT,epoch INTEGER,seq INTEGER,created REAL,digest TEXT,nonce BLOB,payload BLOB);
        CREATE INDEX IF NOT EXISTS target_idx ON cache(workspace,target,created);
        CREATE TABLE IF NOT EXISTS audit(time REAL,kind TEXT,id TEXT);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);''');self.db.commit()
    def key(self,h):
        return ':'.join(map(str,[h['workspace'],h['from'],h.get('stream',''),h['epoch'],h['block_seq']])) if h['kind']=='eeg_block' else ':'.join([h['workspace'],h['from'],h['boot'],str(h['seq'])])
    def put(self,h,body):
        identity=self.key(h);digest=stable_digest(h,body)
        with self.lock:
            previous=self.db.execute('SELECT digest FROM cache WHERE id=?',(identity,)).fetchone()
            if previous:
                if previous[0]!=digest:raise ValueError('Duplicate cached identity changed')
                return dict(duplicate=True,cache_id=identity)
            payload=pack(h,body);used=self.db.execute('SELECT coalesce(sum(length(payload)),0) FROM cache').fetchone()[0]
            if used+len(payload)+16>self.limit or shutil.disk_usage(self.root).free-len(payload)<self.reserve:raise CapacityError('Cloud cache full; keep local journal and retry')
            nonce=os.urandom(12);cipher=self.aes.encrypt(nonce,payload,identity.encode())
            self.db.execute('INSERT INTO cache VALUES(?,?,?,?,?,?,?,?,?,?,?)',(identity,h['workspace'],h['from'],h['to'],h.get('stream',''),h['epoch'],h['block_seq'],time.time(),digest,nonce,cipher));self.db.commit()
            return dict(duplicate=False,cache_id=identity)
    def pending(self,workspace,target,limit=64):
        with self.lock:
            rows=self.db.execute('SELECT id,nonce,payload FROM cache WHERE workspace=? AND target=? ORDER BY created LIMIT ?',(workspace,target,limit)).fetchall()
        result=[]
        for identity,nonce,cipher in rows:
            try:result.append(unpack(self.aes.decrypt(nonce,cipher,identity.encode())))
            except Exception:
                with self.lock:
                    self.db.execute('UPDATE cache SET target=? WHERE id=?',('quarantine',identity))
                    self.db.execute('INSERT INTO audit VALUES(?,?,?)',(time.time(),'corrupt_encrypted_cache_local_retransmission_required',identity));self.db.commit()
                self.last_error='Corrupt encrypted cache quarantined: '+identity
        return result
    def ack(self,workspace,source,stream,epoch,seq):
        with self.lock:
            self.db.execute('DELETE FROM cache WHERE workspace=? AND source=? AND stream=? AND epoch=? AND seq=?',(workspace,source,stream,epoch,seq));self.db.commit()
    def ack_event(self,identity):
        with self.lock:self.db.execute('DELETE FROM cache WHERE id=?',(identity,));self.db.commit()
    def quarantine(self,workspace,source,stream,epoch,seq):
        with self.lock:
            rows=self.db.execute('SELECT id FROM cache WHERE workspace=? AND source=? AND stream=? AND epoch=? AND seq=?',(workspace,source,stream,epoch,seq)).fetchall()
            self.db.execute('UPDATE cache SET target=? WHERE workspace=? AND source=? AND stream=? AND epoch=? AND seq=?',('quarantine',workspace,source,stream,epoch,seq))
            self.db.executemany('INSERT INTO audit VALUES(?,?,?)',[(time.time(),'hub_rejected_quarantined',r[0]) for r in rows]);self.db.commit()
    def expire(self):
        with self.lock:
            rows=self.db.execute('SELECT id FROM cache WHERE created<?',(time.time()-self.ttl,)).fetchall()
            self.db.executemany('INSERT INTO audit VALUES(?,?,?)',[(time.time(),'expired_local_retransmission_required',r[0]) for r in rows])
            self.db.execute('DELETE FROM cache WHERE created<?',(time.time()-self.ttl,))
            self.db.execute('DELETE FROM audit WHERE rowid NOT IN (SELECT rowid FROM audit ORDER BY rowid DESC LIMIT 10000)');self.db.commit();self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            return len(rows)
    def snapshot(self):
        with self.lock:
            count,size=self.db.execute('SELECT count(*),coalesce(sum(length(payload)),0) FROM cache').fetchone()
            quarantined=self.db.execute("SELECT count(*) FROM cache WHERE target='quarantine'").fetchone()[0]
            return dict(blocks=count,quarantined=quarantined,bytes=size,limit_bytes=self.limit,ttl_s=self.ttl,reserve_bytes=self.reserve,free_bytes=shutil.disk_usage(self.root).free)
    def close(self):
        with self.lock:self.db.close()
