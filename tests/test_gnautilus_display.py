import unittest
import numpy as np
from gnautilus_acquisition.display import PreviewBuffer,EventLog,envelope_points
from tests.test_gnautilus_transport import descriptor


class DisplayTests(unittest.TestCase):
    def test_preview_is_bounded_and_preserves_raw_values_and_nan(self):
        p=PreviewBuffer(seconds=2);p.configure(descriptor(fs=10))
        x=np.arange(960,dtype=np.float32).reshape(30,32);x[-2,1]=np.nan;p.push(x,123)
        s=p.snapshot();self.assertEqual(s['samples'].shape,(20,32));self.assertEqual(s['samples'][0,0],x[10,0]);self.assertTrue(np.isnan(s['samples'][-2,1]))
        s['samples'][0,0]=-1;self.assertNotEqual(p.snapshot()['samples'][0,0],-1)
    def test_envelope_keeps_short_peaks_without_connecting_invalid_gaps(self):
        x=np.zeros(1000);x[33]=100;x[500]=np.nan;x[700]=-80
        segments=envelope_points(x,50)
        self.assertGreaterEqual(len(segments),2)
        y=[p[1] for s in segments for p in s];self.assertIn(100,y);self.assertIn(-80,y)
    def test_vectorized_envelope_matches_peak_order_on_partial_and_nonfinite_runs(self):
        import math
        rng=np.random.default_rng(9)
        for length in [1,2,127,1250]:
            original=rng.normal(size=length*2);a=original[::2]
            if length>8:a[3]=np.nan;a[7]=np.inf
            before=a.copy()
            for pixels in [1,5,500]:
                valid=np.isfinite(a);edges=np.flatnonzero(np.diff(np.r_[False,valid,False]));stride=max(1,math.ceil(len(a)/pixels));expected=[]
                for start,end in zip(edges[::2],edges[1::2]):
                    points=[]
                    for begin in range(start,end,stride):
                        part=a[begin:min(begin+stride,end)]
                        for offset in sorted({0,int(np.argmin(part)),int(np.argmax(part)),len(part)-1}):points.append((begin+offset,float(part[offset])))
                    expected.append(points)
                self.assertEqual(envelope_points(a,pixels),expected)
            np.testing.assert_array_equal(a,before)

    def test_logs_are_bounded_and_have_stage_and_severity(self):
        log=EventLog(2);log.add('采集','INFO','one');log.add('中枢','INFO','two');log.add('写盘','ERROR','three')
        records=log.snapshot();self.assertEqual(len(records),2);self.assertEqual(records[-1]['level'],'ERROR');self.assertEqual(records[-1]['stage'],'写盘')


if __name__=='__main__':unittest.main()
