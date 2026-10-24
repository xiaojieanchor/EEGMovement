"""Durable tasks and model registration. A task cannot finish before registration."""
import json,sqlite3,threading,time,uuid
from contextlib import contextmanager
from pathlib import Path
from .common import encoded,digest

STATES={'queued','preparing','waiting_resource','running','evaluating','exporting','registering','completed','failed','cancelled','interrupted'}
TERMINAL={'completed','failed','cancelled'}
class Registry:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock()
        with self.db() as c:
            c.executescript('PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, request_key TEXT UNIQUE, spec TEXT, state TEXT, model_id TEXT, attempt INTEGER, created REAL, updated REAL); CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, message TEXT, data TEXT, utc REAL); CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,path TEXT,manifest TEXT,created REAL); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);')
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def create_job(self,spec,request_key):
        if not isinstance(request_key,str) or not 1<=len(request_key)<=128:raise ValueError('Idempotency key required')
        with self.lock,self.db() as c:
            row=c.execute('SELECT * FROM jobs WHERE request_key=?',(request_key,)).fetchone()
            if row:
                if json.loads(row['spec'])!=spec:raise ValueError('Repeated job request changed')
                return self._job(row)
            jid=uuid.uuid4().hex;now=time.time()
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)',(jid,request_key,encoded(spec).decode(),'queued',None,1,now,now))
        return self.job(jid)
    def _job(self,row):
        if row is None:raise ValueError('Job not found')
        row=dict(row);row['spec']=json.loads(row['spec']);return row
    def job(self,jid):
        with self.db() as c:return self._job(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
    def jobs(self):
        # Active tasks are always visible, regardless of the retained history size.
        with self.db() as c:return [self._job(r) for r in c.execute("SELECT * FROM jobs WHERE state NOT IN ('completed','cancelled','failed') OR id IN (SELECT id FROM jobs ORDER BY created DESC LIMIT 200) ORDER BY created DESC")]
    def pending_jobs(self):
        with self.db() as c:return [self._job(r) for r in c.execute("SELECT * FROM jobs WHERE state IN ('queued','waiting_resource') ORDER BY created")]
    def interrupted_candidates(self):
        with self.db() as c:return [self._job(r) for r in c.execute("SELECT * FROM jobs WHERE state IN ('preparing','running','evaluating','exporting','registering')")]
    def transition(self,jid,state):
        if state not in STATES:raise ValueError('Invalid task phase')
        with self.lock,self.db() as c:
            r=self._job(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
            if r['state'] in TERMINAL and state!=r['state']:raise ValueError('Terminal attempt must be resumed explicitly')
            if state=='completed' and not r['model_id']:raise ValueError('Model must be registered before completion')
            c.execute('UPDATE jobs SET state=?,updated=? WHERE id=?',(state,time.time(),jid))
        self.event(jid,state,{'phase':state});return self.job(jid)
    def event(self,jid,message,data=None):
        with self.lock,self.db() as c:
            c.execute('INSERT INTO events(job_id,message,data,utc) VALUES(?,?,?,?)',(jid,str(message)[:2000],encoded(data or {}).decode(),time.time()))
    def events(self,jid,after=0):
        with self.db() as c:
            return [dict(r, data=json.loads(r['data'])) for r in c.execute('SELECT * FROM events WHERE job_id=? AND seq>? ORDER BY seq LIMIT 500',(jid,int(after)))]
    def register(self,path,manifest,jid=None):
        mid=manifest['model_id']
        with self.lock,self.db() as c:
            old=c.execute('SELECT manifest FROM models WHERE id=?',(mid,)).fetchone()
            if old and json.loads(old['manifest'])!=manifest:raise ValueError('Model identity conflict')
            c.execute('INSERT OR IGNORE INTO models VALUES(?,?,?,?)',(mid,str(path),encoded(manifest).decode(),time.time()))
            if jid:c.execute('UPDATE jobs SET model_id=?,updated=? WHERE id=?',(mid,time.time(),jid))
        return mid
    def models(self):
        with self.db() as c:return [dict(json.loads(r['manifest']),path=r['path']) for r in c.execute('SELECT * FROM models ORDER BY created DESC')]
    def model(self,mid):
        for row in self.models():
            if row['model_id']==mid:return row
        raise ValueError('Model not registered')
    def selected(self):
        with self.db() as c:
            r=c.execute("SELECT value FROM meta WHERE key='selected'").fetchone();return r[0] if r else None
    def select(self,mid):
        self.model(mid)
        with self.lock,self.db() as c:c.execute("INSERT OR REPLACE INTO meta VALUES('selected',?)",(mid,))
    def resume(self,jid):
        with self.lock,self.db() as c:
            r=self._job(c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
            if r['state'] not in ['failed','interrupted','cancelled','waiting_resource']:raise ValueError('Task cannot be resumed in its current phase')
            c.execute('UPDATE jobs SET state=?,attempt=attempt+1,updated=? WHERE id=?',('queued',time.time(),jid))
        self.event(jid,'resumed',{'previous_state':r['state']});return self.job(jid)
