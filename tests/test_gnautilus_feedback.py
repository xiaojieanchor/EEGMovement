import unittest
from gnautilus_acquisition.feedback import HubFeedbackLog


class FeedbackTests(unittest.TestCase):
    def test_repeated_signed_feedback_is_deduplicated_and_hub_restart_is_distinct(self):
        log=HubFeedbackLog();events=[]
        hub=dict(log_boot_id='first',logs=[dict(id=1,stage='标签',level='INFO',message='LEFT已保存')])
        log.consume(hub,lambda *x:events.append(x));log.consume(hub,lambda *x:events.append(x))
        self.assertEqual(len(events),1)
        log.consume(dict(hub,log_boot_id='second'),lambda *x:events.append(x))
        self.assertEqual(len(events),2)

    def test_old_hub_and_invalid_log_fields_do_not_break_status_reception(self):
        log=HubFeedbackLog();events=[];callback=lambda *x:events.append(x)
        log.consume(dict(sample_index_end=128),callback)
        log.consume(dict(log_boot_id='server',logs=[dict(id=True,stage='标签',level='INFO',message='bad'),dict(id=1,stage='private',level='INFO',message='bad'),dict(id=2,stage='记录',level='INFO',message='x'*1000)]),callback)
        self.assertEqual(len(events),1);self.assertLess(len(events[0][2]),280)


if __name__=='__main__':unittest.main()
