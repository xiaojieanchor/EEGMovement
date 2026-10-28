"""Past-only 2-second PSD shape and joint channel covariance evidence."""
import numpy as np

FREQUENCIES=np.arange(8.,30.01,.5)

class CausalSpectrum:
    def __init__(self):
        from scipy.signal import butter
        self.sos=butter(4,[8,30],fs=250,btype='bandpass',output='sos');self.zi=np.zeros((len(self.sos),32,2));self.history=np.empty((32,0));self.samples=0
    def step(self,x):
        from scipy.signal import sosfilt
        from scipy.signal.windows import hann
        x=np.asarray(x,dtype='float64')
        if x.ndim!=2 or x.shape[0]!=32 or not x.shape[1] or not np.isfinite(x).all():raise ValueError('Finite new samples in 32 physical source columns required')
        z,self.zi=sosfilt(self.sos,x,zi=self.zi,axis=-1);self.history=np.concatenate((self.history,z),axis=-1)[:,-500:];self.samples+=x.shape[1];n=self.history.shape[1];centered=self.history-self.history.mean(1,keepdims=True)
        if n<2:power=np.zeros((32,45))
        else:
            window=hann(n,sym=False);psd=2*np.abs(np.fft.rfft(centered*window,axis=-1))**2/(250*np.sum(window**2));freq=np.fft.rfftfreq(n,1/250);power=np.array([np.interp(FREQUENCIES,freq,p) for p in psd])
        cov=centered@centered.T/max(1,n-1);den=np.sqrt(np.maximum(np.diag(cov),1e-10));corr=cov/den[:,None]/den[None,:];a,b=np.triu_indices(32,1)
        return np.concatenate((np.log(np.maximum(power,1e-10)).ravel(),corr[a,b])).astype('float32'),self.samples>=500

def select_view(full,indices,mode):
    x=np.asarray(full);ix=np.asarray(list(indices),dtype=int)
    if x.shape[-1]!=1936 or len(ix)<2 or len(set(ix))!=len(ix) or ix.min()<0 or ix.max()>=32:raise ValueError('Fine PSD physical channel identity differs')
    power=x[...,:1440].reshape(x.shape[:-1]+(32,45))[...,ix,:]
    if mode=='shape':power=power-power.mean(-1,keepdims=True)
    elif mode!='absolute':raise ValueError('Unknown fine PSD view')
    corr=np.broadcast_to(np.eye(32,dtype=x.dtype),x.shape[:-1]+(32,32)).copy();a,b=np.triu_indices(32,1);corr[...,a,b]=x[...,1440:];corr[...,b,a]=x[...,1440:];pairs=corr[...,ix[:,None],ix[None,:]];a,b=np.triu_indices(len(ix),1)
    return np.concatenate((power.reshape(x.shape[:-1]+(-1,)),pairs[...,a,b]),axis=-1).astype('float32')

class FineSpectrumEvidence:
    def __init__(self,base,mode):self.base=base;self.mode=mode;self.indices=base.indices;self.psd=CausalSpectrum()
    @property
    def samples(self):return self.base.samples
    def step(self,x):
        _,h,ready=self.base.step(x);full,available=self.psd.step(x);return select_view(full,self.indices,self.mode),h,ready and available
