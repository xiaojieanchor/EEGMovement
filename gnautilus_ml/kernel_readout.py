"""Small RBF readout on frozen multichannel features; no source-label remapping."""
import numpy as np

def kernel_features(x,arrays,prefix=''):
    mean=arrays[prefix+'kernel_mean'];scale=arrays[prefix+'kernel_scale'];centers=arrays[prefix+'kernel_centers'];gamma=float(arrays[prefix+'kernel_gamma'])
    if gamma<=0 or (scale<=0).any():raise ValueError('Invalid fitted kernel normalization')
    z=(np.asarray(x)-mean)/scale;return np.exp(-gamma*np.mean((z[...,None,:]-centers)**2,axis=-1)).astype('float32')

def fit_kernel(x,y,ids,gamma):
    from .governance import authorize_training
    from .intent_repair_training import balanced
    authorize_training();w=balanced(y,ids);mean=(x*w[:,None]).sum(0);scale=np.sqrt(np.maximum(((x-mean)**2*w[:,None]).sum(0),1e-6));z=(x-mean)/scale;centers=[]
    for trial in np.unique(ids):
        rows=z[ids==trial]
        for q in [.2,.5,.8]:centers.append(rows[min(len(rows)-1,int(q*len(rows)))])
    return dict(kernel_mean=mean.astype('float32'),kernel_scale=scale.astype('float32'),kernel_centers=np.asarray(centers,dtype='float32'),kernel_gamma=np.asarray(gamma,dtype='float32'))
