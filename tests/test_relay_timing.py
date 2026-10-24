import unittest
from eeg_relay.timing import associate

class Timing(unittest.TestCase):
    def anchor(self,start,end,read_end_ms):return dict(stream='a'*32,epoch=3,start=start,end=end,rate=500,read_end_ms=read_end_ms,uncertainty_ms=4)
    def test_sample_interval_estimate_and_unknown(self):
        a=[self.anchor(0,50,1000),self.anchor(50,100,1100)]
        r=associate(a,1050,2,3);self.assertEqual(r['status'],'host_clock_estimate');self.assertEqual(r['sample_range'],[72,78]);self.assertFalse(r['hardware_synchronization'])
        self.assertEqual(associate(a,1050,2,4)['status'],'unresolved')
        self.assertEqual(associate(a,1300,2,3)['status'],'unresolved')
    def test_gap_does_not_gain_continuity(self):
        a=[self.anchor(0,50,1000),self.anchor(60,110,1100)]
        self.assertEqual(associate(a,1000,20,3)['status'],'unresolved')

if __name__=='__main__':unittest.main()
