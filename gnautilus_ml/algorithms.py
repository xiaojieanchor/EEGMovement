"""Inspectable finite-history classifiers. Every fit requires governance authority."""
import numpy as np
from scipy.linalg import eigh
from scipy.signal import butter,sosfilt
from .governance import authorize_training,compute_device

ALGORITHMS={
 'bandpower_lda':'频带功率 / 收缩LDA','csp_lda':'正则化CSP / 收缩LDA',
 'gaze_lda':'前额低频方向 / LDA','cnn_gru':'因果轻量CNN / 单向GRU','hybrid':'眼动与运动想象 / 后融合'}
ALGORITHMS['cnn_gru_baseline']='因果去基线 / 频带CNN＋GRU'
ALGORITHMS['causal_jepa_stream']='联合导航 / 有状态JEPA'
from .multistage import METHODS
ALGORITHMS.update(METHODS)
FRONTAL={'Fp1','Fp2','Fpz','AF3','AF4','AF7','AF8','F7','F8','F3','F4'}

def filtered(x,fs,band):
    if band[1]>=fs/2:raise ValueError('Sampling rate does not support requested analysis band')
    return sosfilt(butter(4,band,btype='bandpass',fs=fs,output='sos'),x,axis=-1)
def covariance(x):
    z=x-x.mean(-1,keepdims=True);c=np.einsum('nct,ndt->ncd',z,z)
    trace=np.trace(c,axis1=1,axis2=2)
    if np.any(trace<=1e-20):raise ValueError('Zero-power trial')
    return c/trace[:,None,None]
def shrink(c,a=.1):return (1-a)*c+a*np.trace(c)/len(c)*np.eye(len(c))
def balanced(y,p):
    return float(np.mean([np.mean(p[y==c]==c) for c in np.unique(y)]))

class Classifier:
    def __init__(self,algorithm,channels,fs,window_s=1.5,seed=17,max_epochs=50):
        if algorithm=='causal_jepa_stream':raise ValueError('Use the whole-episode personal JEPA study entrypoint')
        if algorithm not in ALGORITHMS:raise ValueError('Unsupported classifier')
        if len(set(channels))!=len(channels) or not 1<=len(channels)<=64 or any(not isinstance(c,str) or not 1<=len(c)<=120 for c in channels):raise ValueError('Unique channel positions required')
        if not np.isfinite(fs) or not 1<=fs<=10000 or not np.isfinite(window_s) or not .25<=window_s<=5:raise ValueError('Invalid model sampling/history contract')
        self.algorithm=algorithm;self.channels=list(channels);self.fs=float(fs);self.window_s=window_s;self.seed=seed;self.max_epochs=max_epochs
        self.arrays={};self.details={};self.classes=None
    def _x(self,x):
        a=np.asarray(x,dtype=np.float32)
        if a.ndim!=3 or a.shape[1]!=len(self.channels) or a.shape[2]!=round(self.fs*self.window_s) or not np.isfinite(a).all():raise ValueError('Finite windows with declared channel/rate/history required')
        return a
    def _base(self,x):
        if self.algorithm=='gaze_lda':
            indices=[i for i,c in enumerate(self.channels) if c in FRONTAL]
            if len(indices)<2:raise ValueError('At least two named frontal scalp channels required')
            z=filtered(x[:,indices],self.fs,(.5,5));n=z.shape[-1];quarter=max(1,n//4)
            # Signed level, change, and slope are retained; no absolute-value eye direction.
            return np.concatenate([z.mean(-1),z[:,:,-quarter:].mean(-1)-z[:,:,:quarter].mean(-1),np.mean(np.diff(z,axis=-1),axis=-1)],axis=1)
        indices=[i for i,c in enumerate(self.channels) if c not in FRONTAL]
        if len(indices)<2:raise ValueError('At least two named nonfrontal channels required')
        z=x[:,indices]
        if self.algorithm=='bandpower_lda':return np.concatenate([np.log10(np.maximum(np.mean(filtered(z,self.fs,b)**2,axis=-1),1e-20)) for b in [(8,12),(16,24)]],axis=1)
        z=filtered(z,self.fs,(8,30));v=np.var(np.einsum('kc,nct->nkt',self.arrays['spatial'],z),axis=-1)
        return np.log10(np.maximum(v/np.maximum(v.sum(1,keepdims=True),1e-20),1e-20))
    def _fit_lda(self,f,y):
        self.arrays['mean']=f.mean(0);self.arrays['scale']=np.maximum(f.std(0),1e-7)
        f=(f-self.arrays['mean'])/self.arrays['scale'];means=np.array([f[y==c].mean(0) for c in self.classes])
        cov=sum((f[y==c]-m).T@(f[y==c]-m) for c,m in zip(self.classes,means))/max(1,len(y)-len(self.classes))
        cov=shrink(cov)+np.eye(len(cov))*1e-8;weight=np.linalg.solve(cov,means.T)
        self.arrays['weight']=weight;self.arrays['bias']=-.5*np.sum(means*weight.T,axis=1)
    def fit(self,x,y,groups=None,progress=None,validation=None):
        authorize_training();x=self._x(x);y=np.asarray(y);self.classes=np.unique(y)
        if len(y)!=len(x) or len(self.classes)<2 or min(np.sum(y==c) for c in self.classes)<3:raise ValueError('At least three trials per supported class required')
        if self.algorithm in METHODS:
            from .multistage import fit_multistage
            return fit_multistage(self,x,y,groups,progress)
        if self.algorithm=='hybrid':return self._fit_hybrid(x,y,groups,progress)
        if self.algorithm in ['cnn_gru','cnn_gru_baseline']:
            from .network import fit_network
            if self.algorithm=='cnn_gru_baseline':
                x=self._neural_input(x)
                if validation is not None:validation=(self._neural_input(validation[0]),validation[1],validation[2])
            self.arrays,self.details=fit_network(x,y,self.classes,self.seed,self.max_epochs,compute_device(),progress,getattr(self,'checkpoint_path',None),validation=validation,training_groups=groups)
            if self.algorithm=='cnn_gru_baseline':self.details['preprocessing']='finite_window_first_10pct_baseline_then_causal_0.5_30Hz_v1'
            return self
        if self.algorithm=='csp_lda':
            indices=[i for i,c in enumerate(self.channels) if c not in FRONTAL];cov=covariance(filtered(x[:,indices],self.fs,(8,30)));parts=[]
            for label in self.classes:
                a=shrink(cov[y==label].mean(0));b=shrink(cov[y!=label].mean(0));_,vectors=eigh(a,a+b)
                k=min(4,len(indices));ix=list(range(k//2))+list(range(len(indices)-(k-k//2),len(indices)));parts.append(vectors[:,ix].T)
            self.arrays['spatial']=np.concatenate(parts)
        self._fit_lda(self._base(x),y);self.details['score_semantics']='uncalibrated_lda_posterior';return self
    def _fit_hybrid(self,x,y,groups,progress):
        if groups is None or len(np.unique(groups))<3:raise ValueError('Fusion needs at least three training session groups for out-of-fold fitting')
        groups=np.asarray(groups);a=np.zeros((len(y),len(self.classes)));b=a.copy()
        for group in np.unique(groups):
            keep=groups!=group
            if not np.array_equal(np.unique(y[keep]),self.classes):raise ValueError('Fusion fold lacks class support')
            for alg,target in [('csp_lda',a),('gaze_lda',b)]:
                m=Classifier(alg,self.channels,self.fs,self.window_s).fit(x[keep],y[keep]);target[~keep]=m.predict(x[~keep])['probabilities']
        choices=[(balanced(y,self.classes[np.argmax(w*a+(1-w)*b,axis=1)]),w) for w in [0.,.25,.5,.75,1.]]
        self.details={'fusion_weight':max(choices)[1],'fusion_selection':'training_group_out_of_fold','score_semantics':'uncalibrated_late_fusion'}
        for prefix,alg in [('motor','csp_lda'),('gaze','gaze_lda')]:
            child=Classifier(alg,self.channels,self.fs,self.window_s).fit(x,y)
            self.arrays.update({prefix+'__'+k:v for k,v in child.arrays.items()})
        return self
    def _neural_input(self,x):
        baseline=x[:,:,:max(1,x.shape[-1]//10)].mean(-1,keepdims=True)
        return filtered(x-baseline,self.fs,(.5,30)).astype(np.float32)
    def predict(self,x):
        x=self._x(x)
        if self.classes is None:raise ValueError('Unfitted classifier')
        branches=None
        if self.algorithm in METHODS:
            from .multistage import predict_multistage
            p=predict_multistage(self,x)
        elif self.algorithm=='hybrid':
            branches={}
            for prefix,alg in [('motor','csp_lda'),('gaze','gaze_lda')]:
                child=Classifier(alg,self.channels,self.fs,self.window_s);child.classes=self.classes;child.arrays={k.split('__',1)[1]:v for k,v in self.arrays.items() if k.startswith(prefix+'__')}
                branches[prefix]=child.predict(x)['probabilities']
            w=self.details['fusion_weight'];p=w*branches['motor']+(1-w)*branches['gaze']
        elif self.algorithm in ['cnn_gru','cnn_gru_baseline']:
            from .network import Predictor
            if not hasattr(self,'_network_predictor'):self._network_predictor=Predictor(self.arrays,len(self.channels),len(self.classes))
            if self.algorithm=='cnn_gru_baseline':x=self._neural_input(x)
            p=self._network_predictor(x)
        else:
            f=(self._base(x)-self.arrays['mean'])/self.arrays['scale'];z=f@self.arrays['weight']+self.arrays['bias'];z-=z.max(1,keepdims=True);p=np.exp(z);p/=p.sum(1,keepdims=True)
        policy=self.details.get('acceptance_policy') or {};ordered=np.sort(p,axis=1)
        accepted=(p.max(1)>=policy.get('threshold',0.))&((ordered[:,-1]-ordered[:,-2])>=policy.get('margin',0.))
        return dict(labels=self.classes[p.argmax(1)],probabilities=p,accepted=accepted,branches=branches,score_semantics=self.details.get('score_semantics','uncalibrated_neural_softmax'))
