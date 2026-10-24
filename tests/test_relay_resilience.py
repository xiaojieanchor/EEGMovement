import tempfile,threading,unittest,time
from eeg_relay.endpoint import Endpoint
from eeg_relay.protocol import seal
from eeg_relay.spool import Spool

class Resilience(unittest.TestCase):
    def endpoint(self):
        got=[];e=Endpoint(dict(workspace='a'*32,device_id='c'*32,secret='ef'*32),lambda *a:got.append(a));e.route_server='f'*32
        return e,got
    def test_blocked_history_does_not_lock_control_or_stop(self):
        e,_=self.endpoint();blocked=threading.Event();release=threading.Event();sent=threading.Event()
        class History:
            def send(self,*args):blocked.set();release.wait(2)
            def close(self):release.set()
        class Control:
            def send(self,*args):sent.set()
            def close(self):pass
        e.connections={'control':Control(),'backfill':History()};e.state['connected']=True
        thread=threading.Thread(target=lambda:e.send('clock',b'{}','b'*32,'ef'*32,lane='backfill'));thread.start();self.assertTrue(blocked.wait(1))
        control=threading.Thread(target=lambda:e.send('clock',b'{}','b'*32,'ef'*32));control.start()
        self.assertTrue(sent.wait(.3),'Independent control lane blocked by history write')
        e.stop();thread.join(1);control.join(1);self.assertFalse(thread.is_alive())
    def test_many_authenticated_generations_late_old_and_conflicting_boot(self):
        e,got=self.endpoint();frames=[]
        for generation in range(1,131):
            h=seal('a'*32,'b'*32,'c'*32,format(generation,'032x'),1,'hub_status',b'{}','ef'*32)
            frames.append((dict(h,route_server='f'*32,route_generation=generation),b'{}'))
        frames+=[frames[0],frames[-1]]
        class Input:
            def receive(self,*_):
                if frames:return frames.pop(0)
                raise EOFError()
        e.read('control',Input());self.assertEqual(len(got),130);self.assertEqual(len(e.replays),1)
        e.broken.clear();h=seal('a'*32,'b'*32,'c'*32,'d'*32,2,'hub_status',b'{}','ef'*32)
        frames.append((dict(h,route_server='f'*32,route_generation=130),b'{}'));e.read('control',Input())
        self.assertIn('Authenticated boot conflict',e.state['error']);self.assertEqual(len(got),130)
    def test_corrupt_cache_is_quarantined_and_healthy_blocks_replay(self):
        with tempfile.TemporaryDirectory() as root:
            s=Spool(root,b'k'*32,reserve=0)
            for seq in range(1,4):
                h=seal('a'*32,'b'*32,'c'*32,'d'*32,seq,'eeg_block',b'fixture','ef'*32,stream='e'*32,epoch=0,block_seq=seq);s.put(h,b'fixture')
            with s.lock:s.db.execute("UPDATE cache SET payload=? WHERE seq=2",(b'broken',));s.db.commit()
            self.assertEqual([h['block_seq'] for h,_ in s.pending('a'*32,'c'*32)],[1,3]);self.assertEqual(s.snapshot()['quarantined'],1)
            self.assertIn('quarantined',s.last_error);self.assertEqual(s.snapshot()['blocks'],3);s.close()

if __name__=='__main__':unittest.main()
