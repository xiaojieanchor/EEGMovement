"""Causal joint-channel SPD descriptors, with no fitted target-domain statistics."""
import numpy as np

class CausalCovariance:
    def __init__(self):
        from scipy.signal import butter
        self.sos=[butter(4,b,fs=250,btype='bandpass',output='sos') for b in [[8,13],[13,30]]];self.zi=[np.zeros((len(s),32,2)) for s in self.sos];self.history=np.empty((2,32,0));self.samples=0
    def step(self,x):
        from scipy.signal import sosfilt
        x=np.asarray(x,dtype='float64')
        if x.ndim!=2 or x.shape[0]!=32 or not x.shape[1] or not np.isfinite(x).all():raise ValueError('Finite new 32 physical channel samples required')
        bands=[]
        for i,s in enumerate(self.sos):z,self.zi[i]=sosfilt(s,x,zi=self.zi[i],axis=-1);bands.append(z)
        self.history=np.concatenate((self.history,np.asarray(bands)),axis=-1)[:,:,-1000:];self.samples+=x.shape[1];cov=[]
        for size in [500,1000]:
            z=self.history[:,:,-size:];z=z-z.mean(-1,keepdims=True);cov.append(z@z.swapaxes(-1,-2)/max(1,z.shape[-1]-1))
        return np.asarray(cov).ravel().astype('float32'),self.samples>=500

def select_view(full,indices,mode,window=None):
    if window is None:mode,size=mode.rsplit('_',1);window=int(size)
    x=np.asarray(full,dtype='float64');ix=np.asarray(list(indices),dtype=int)
    if x.shape[-1]!=4096 or window not in [500,1000] or len(ix)<2 or len(set(ix))!=len(ix) or ix.min()<0 or ix.max()>=32:raise ValueError('Covariance window/physical identity differs')
    c=x.reshape(x.shape[:-1]+(2,2,32,32))[...,0 if window==500 else 1,:,:,:];c=c[...,ix[:,None],ix[None,:]];n=len(ix)
    if mode=='logcorr':
        d=np.sqrt(np.maximum(np.diagonal(c,axis1=-2,axis2=-1),1e-12));c=c/d[...,None]/d[...,None,:]
    elif mode=='logcov':c=c/np.maximum(np.trace(c,axis1=-2,axis2=-1)[...,None,None]/n,1e-12)
    else:raise ValueError('Unknown SPD view')
    # Fixed shrinkage ensures a positive definite matrix, even for short history.
    c=.95*c+.05*np.eye(n);e,v=np.linalg.eigh(c);log=(v*np.log(np.maximum(e,1e-7))[...,None,:])@v.swapaxes(-1,-2);a,b=np.triu_indices(n);features=log[...,a,b]*np.where(a==b,1.,np.sqrt(2.));return features.reshape(x.shape[:-1]+(-1,)).astype('float32')

class CovarianceEvidence:
    def __init__(self,base,mode):self.base=base;self.mode=mode;self.indices=base.indices;self.cov=CausalCovariance();self.required=int(mode.rsplit('_',1)[1])
    @property
    def samples(self):return self.base.samples
    def step(self,x):
        _,h,ready=self.base.step(x);full,_=self.cov.step(x);return select_view(full,self.indices,self.mode),h,ready and self.samples>=self.required
