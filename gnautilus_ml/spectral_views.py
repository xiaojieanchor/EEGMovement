"""Causal multichannel feature views. Physical source columns never move."""
import numpy as np

def full_spectrum(history):
    h=np.asarray(history);values=[]
    if h.ndim!=3 or h.shape[:2]!=(2,32) or h.shape[-1]<1:raise ValueError('Two frequency bands and 32 physical channels required')
    for n in [125,375,750,1500]:values.append(np.log(np.maximum(np.mean(h[:,:,-n:]**2,axis=-1),1e-8)).ravel())
    v=h[:,:,-750:].sum(0);v=v-v.mean(1,keepdims=True);cov=v@v.T/max(1,v.shape[1]-1);den=np.sqrt(np.maximum(np.diag(cov),1e-8));corr=cov/den[:,None]/den[None,:];values.append(corr[np.triu_indices(32,1)]);return np.concatenate(values).astype('float32')

def select_view(full,indices,mode):
    x=np.asarray(full);ix=np.asarray(list(indices),dtype=int)
    if x.shape[-1]!=752 or len(ix)<2 or len(set(ix))!=len(ix) or ix.min()<0 or ix.max()>=32:raise ValueError('Ordered physical feature identity differs')
    power=x[...,:256].reshape(x.shape[:-1]+(4,2,32))[...,ix];corr=np.broadcast_to(np.eye(32,dtype=x.dtype),x.shape[:-1]+(32,32)).copy();a,b=np.triu_indices(32,1);corr[...,a,b]=x[...,256:];corr[...,b,a]=x[...,256:];c=corr[...,ix[:,None],ix[None,:]];u,v=np.triu_indices(len(ix),1);pair=c[...,u,v]
    relative=power-power.mean(-1,keepdims=True);ratio=power[...,0,:]-power[...,1,:];contrast=power[...,:3,:,:]-power[...,3:4,:,:]
    flatten=lambda z:z.reshape(z.shape[:len(x.shape)-1]+(-1,))
    if mode=='absolute':parts=[flatten(power)]
    elif mode=='spatial_relative':parts=[flatten(relative)]
    elif mode=='band_ratio':parts=[flatten(ratio)]
    elif mode=='temporal_contrast':parts=[flatten(contrast),flatten(relative[...,3,:,:])]
    elif mode=='combined':parts=[flatten(relative),flatten(ratio),flatten(contrast)]
    else:raise ValueError('Unknown causal spectral view')
    return np.concatenate(parts+[pair],axis=-1).astype('float32')

class SpectrumViewEvidence:
    def __init__(self,base,mode):self.base=base;self.mode=mode;self.indices=base.indices
    @property
    def samples(self):return self.base.samples
    def step(self,x):
        _,h,ready=self.base.step(x);return select_view(full_spectrum(self.base.history),self.indices,self.mode),h,ready
