import unittest
import numpy as np
from gnautilus_ml.ocular_event_training import causal_view, prefix_ends, event_weights, metrics, build_model

class OcularEventTrainingTests(unittest.TestCase):
    def test_causal_real_history_no_padding_or_future(self):
        x=np.arange(32*800,dtype=np.float32).reshape(32,800)
        a=causal_view(x,87);xx=x.copy();xx[:,87:]=-999
        np.testing.assert_array_equal(a,causal_view(xx,87))
        self.assertEqual(a.shape,(32,87));self.assertEqual(causal_view(x,700).shape,(32,500))
        with self.assertRaises(ValueError):causal_view(x,801)

    def test_short_censored_event_always_retains_last_observed_prefix(self):
        self.assertEqual(prefix_ends(500,559),[525,550,559])
        self.assertEqual(prefix_ends(500,512),[512])

    def test_class_and_event_mass_not_overlapping_window_count(self):
        y=np.array([1,1,1,1,1,1,2,2]);ids=np.array(['a','a','a','b','b','b','c','c'])
        w=event_weights(y,ids);self.assertAlmostEqual(w[y==1].sum(),w[y==2].sum())
        self.assertAlmostEqual(w[ids=='a'].sum(),w[ids=='b'].sum())

    def test_metrics_include_no_eye_but_not_navigation_rest(self):
        m=metrics(np.array([0,1,2,3,4]),np.eye(5));self.assertEqual(m['balanced_accuracy'],1.)
        self.assertEqual(m['class_order'][0],'NO_EYE')

    def test_full_montage_joint_gradient_includes_cz_and_c5(self):
        import torch
        net=build_model();x=torch.randn(2,32,100,requires_grad=True);net(x,[100,100]).sum().backward()
        self.assertGreater(float(x.grad[:,15].abs().sum()),0);self.assertGreater(float(x.grad[:,18].abs().sum()),0)
        self.assertEqual(net.branch.spatial.weight.shape[2],32)

    def test_padding_ignored_and_partial_last_pool_keeps_tail_samples(self):
        import torch
        torch.manual_seed(3);net=build_model();x=torch.randn(2,32,100,requires_grad=True)
        y=net(x,[59,100]);xx=x.detach().clone();xx[0,:,59:]=1234
        torch.testing.assert_close(y,net(xx,[59,100]))
        torch.testing.assert_close(y[:1],net(x[:1,:,:59],[59]))
        y[0].sum().backward();self.assertGreater(float(x.grad[0,:,58].abs().sum()),0)
        self.assertEqual(float(x.grad[0,:,59:].abs().sum()),0.)

    def test_excluded_channels_have_exactly_zero_influence(self):
        import torch
        weights=np.zeros(32);weights[[0,1,4,5]]=[1.,.5,1.,.5]
        net=build_model(input_weights=weights);x=torch.randn(2,32,101,requires_grad=True)
        y=net(x,[101,101]);xx=x.detach().clone();xx[:,8:]+=1000
        torch.testing.assert_close(y,net(xx,[101,101]));y.sum().backward()
        self.assertEqual(float(x.grad[:,8:].abs().sum()),0.)
        self.assertGreater(float(x.grad[:,[0,1,4,5]].abs().sum()),0)

if __name__=='__main__':unittest.main()
