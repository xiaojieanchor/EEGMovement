"""Causal numerical feature kernel; does not determine electrical QC eligibility."""
from collections import deque
import numpy as np
from scipy.signal import butter,sosfilt

class CausalFeatures:
    def __init__(self,sample_rate_hz,channels,band=(8,30),window_seconds=1.,order=4):
        rate=float(sample_rate_hz)
        if not np.isfinite(rate) or rate<=0 or not 0<band[0]<band[1]<rate/2:raise ValueError('Explicit valid rate and band required')
        if not channels or len(set(channels))!=len(channels):raise ValueError('Unique fixed channel order required')
        if not np.isfinite(window_seconds) or window_seconds<=0:raise ValueError('Invalid window')
        self.channels=list(channels);self.rate=rate;self.length=max(1,int(round(rate*window_seconds)))
        self.sos=butter(order,band,btype='bandpass',fs=rate,output='sos')
        self.epoch=None;self.end=None;self._reset()

    def _reset(self):
        self.zi=np.zeros((len(self.sos),2,len(self.channels)))
        self.history=deque(maxlen=self.length)

    def process(self,samples,start,epoch,valid=None):
        x=np.asarray(samples,dtype=float)
        if x.ndim!=2 or x.shape[1]!=len(self.channels) or not len(x) or not np.isfinite(x).all():raise ValueError('Finite fixed-channel batch required')
        if type(start) is not int or start<0 or type(epoch) is not int:raise ValueError('Explicit sample identity required')
        if epoch==self.epoch and self.end is not None and start<self.end:raise ValueError('overlap or reversed samples')
        mask=np.ones(len(x),dtype=bool) if valid is None else np.asarray(valid)
        if mask.shape!=(len(x),) or mask.dtype!=np.bool_:raise ValueError('Per-sample boolean validity required')
        reset=epoch!=self.epoch or start!=self.end
        if reset:self._reset()
        out=np.full(x.shape,np.nan);ready=np.zeros(len(x),dtype=bool)
        for i,row in enumerate(x):
            if not mask[i]:self._reset();continue
            z,self.zi=sosfilt(self.sos,row[None,:],axis=0,zi=self.zi)
            self.history.append(z[0]**2)
            if len(self.history)==self.length:
                out[i]=np.mean(self.history,axis=0);ready[i]=True
        self.epoch=epoch;self.end=start+len(x)
        return {'power':out,'ready':ready,'reset':reset,'sample_start':start,'sample_end':self.end,
                'quality_qualification':'not_determined','semantics':'causal_bandpower_no_device_QC'}
