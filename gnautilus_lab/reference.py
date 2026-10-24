"""Reference CSP/log-variance/shrinkage-LDA numerical kernel.

Real EEG fitting is deliberately unavailable until new training governance and
hardware/API contracts exist. Unit tests mock this gate on synthetic arrays only.
There is no production skip flag, environment bypass, device or training runner.
"""
import json
from pathlib import Path
import numpy as np
from scipy.linalg import eigh

def authorize_fit():
    raise RuntimeError('New-device real EEG training governance is not enabled; API and locked protocol required')

class ReferencePipeline:
    def __init__(self,n_components=4,shrinkage=.1):
        if type(n_components) is not int or n_components<2:raise ValueError('At least two CSP components')
        if not 0<shrinkage<=1:raise ValueError('Positive covariance shrinkage required')
        self.n_components=n_components;self.shrinkage=shrinkage;self.channels=None

    def _trials(self,x):
        x=np.asarray(x,dtype=float)
        if x.ndim!=3 or x.shape[0]<1 or x.shape[1]<2 or x.shape[2]<3 or not np.isfinite(x).all():raise ValueError('Finite trials x channels x samples required')
        return x

    def _features(self,x):
        x=self._trials(x);z=np.einsum('kc,nct->nkt',self.spatial,x)
        var=np.var(z,axis=2)
        if np.any(var.sum(axis=1)<=np.finfo(float).eps):raise ValueError('No supported power')
        return np.log(np.maximum(var/var.sum(axis=1,keepdims=True),np.finfo(float).tiny))

    def fit(self,x,y,channels):
        authorize_fit()
        x=self._trials(x);y=np.asarray(y)
        if y.shape!=(len(x),) or len(np.unique(y))!=2:raise ValueError('Exactly two supported native classes required')
        if len(channels)!=x.shape[1] or len(set(channels))!=len(channels):raise ValueError('Fixed unique channels required')
        self.channels=list(channels);self.classes=np.unique(y)
        if min(np.sum(y==c) for c in self.classes)<2:raise ValueError('Each class needs at least two trials')
        centered=x-x.mean(axis=2,keepdims=True);cov=np.einsum('nct,ndt->ncd',centered,centered)
        tr=np.trace(cov,axis1=1,axis2=2)
        if np.any(tr<=np.finfo(float).eps):raise ValueError('Zero signal trial')
        cov/=tr[:,None,None]
        def regularize(a):return (1-self.shrinkage)*a+self.shrinkage*np.trace(a)/len(a)*np.eye(len(a))
        a,b=[regularize(cov[y==c].mean(axis=0)) for c in self.classes]
        values,vectors=eigh(a,a+b)
        k=min(self.n_components,len(channels));indices=list(range(k//2))+list(range(len(channels)-(k-k//2),len(channels)))
        self.spatial=vectors[:,indices].T
        f=self._features(x);self.feature_mean=f.mean(axis=0);self.feature_scale=np.maximum(f.std(axis=0),1e-8)
        f=(f-self.feature_mean)/self.feature_scale
        means=[f[y==c].mean(axis=0) for c in self.classes]
        pooled=sum((f[y==c]-m).T@(f[y==c]-m) for c,m in zip(self.classes,means))/(len(y)-2)
        pooled=regularize(pooled)+np.eye(k)*1e-9
        self.weight=np.linalg.solve(pooled,means[1]-means[0]);self.bias=-.5*(means[1]+means[0])@self.weight
        return self

    def predict(self,x,channels):
        if self.channels is None or list(channels)!=self.channels:raise ValueError('Channel identity/order changed')
        x=self._trials(x)
        if x.shape[1]!=len(self.channels):raise ValueError('CSP dimension changed')
        f=(self._features(x)-self.feature_mean)/self.feature_scale;margin=f@self.weight+self.bias
        return {'labels':self.classes[(margin>0).astype(int)],'margin':margin,'score_semantics':'uncalibrated_lda_margin'}

    def save(self,path):
        if self.channels is None:raise ValueError('Unfitted')
        meta=json.dumps({'schema':'gnautilus-reference-math-v1','channels':self.channels,
                         'n_components':self.n_components,'shrinkage':self.shrinkage,
                         'score_semantics':'uncalibrated_lda_margin','runtime_accepted':False})
        np.savez(Path(path),spatial=self.spatial,classes=self.classes,feature_mean=self.feature_mean,
                 feature_scale=self.feature_scale,weight=self.weight,bias=self.bias,metadata=meta)

    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as z:
            meta=json.loads(str(z['metadata']))
            if meta.get('schema')!='gnautilus-reference-math-v1' or meta.get('score_semantics')!='uncalibrated_lda_margin':raise ValueError('Unknown model schema')
            m=cls(meta['n_components'],meta['shrinkage']);m.channels=meta['channels']
            for key in ['spatial','classes','feature_mean','feature_scale','weight','bias']:setattr(m,key,z[key].copy())
        return m
