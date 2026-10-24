"""Bounded deduplication of authenticated, acquisition-only Hub log events."""
import threading


class HubFeedbackLog:
    def __init__(self):
        self.lock=threading.Lock();self.boot=None;self.cursor=0

    def consume(self,hub,on_event):
        boot=hub.get('log_boot_id');rows=hub.get('logs')
        if not isinstance(boot,str) or not 1<=len(boot)<=64 or not isinstance(rows,list):return
        pending=[]
        with self.lock:
            if boot!=self.boot:self.boot=boot;self.cursor=0
            valid=[r for r in rows[:32] if isinstance(r,dict) and type(r.get('id')) is int and 0<r['id']<=2**53]
            for row in sorted(valid,key=lambda r:r['id']):
                if row['id']<=self.cursor:continue
                stage=row.get('stage');level=row.get('level');message=row.get('message')
                if stage not in ('接收','记录','标签','连接') or level not in ('INFO','WARN','ERROR') or not isinstance(message,str):continue
                self.cursor=row['id'];pending.append(('中枢·'+stage,level,'中枢日志 #'+str(row['id'])+' · '+message[:240]))
        for event in pending:on_event(*event)
