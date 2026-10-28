"""Persistent navigation evidence plus frozen human-labelled eye event head."""
import copy
import numpy as np
from .navigation_contract import CHANNELS,ELECTRICAL

RUNTIME='gnautilus.hybrid_navigation.v1'
ALGORITHM='hybrid_navigation'

class EventLatch:
    def __init__(self):self.candidate=0;self.count=0;self.latched=0;self.quiet=0
    def step(self,scores):
        p=np.asarray(scores);order=np.argsort(p);d=int(order[-1]);strong=d>0 and p[d]>=.75 and p[d]-p[order[-2]]>=.15
        if not strong:
            self.candidate=0;self.count=0;self.quiet+=1
            if self.quiet>=2:self.latched=0
            return 0
        self.quiet=0
        if d==self.latched:return 0
        self.count=self.count+1 if d==self.candidate else 1;self.candidate=d
        if self.count<3:return 0
        self.latched=d;self.candidate=0;self.count=0;return d

class GazePolicy:
    def __init__(self):self.reset()
    def reset(self):self.cue=0;self.pending=0;self.pending_count=0;self.active=0;self.candidate=0;self.count=0;self.weak=0
    def eye_event(self,d):
        if d not in [1,2,3,4]:return
        if self.cue and d=={1:2,2:1,3:4,4:3}[self.cue]:self.cue=0;self.pending=d;self.pending_count=0;self.active=0;self.candidate=0;self.count=0
        elif d!=self.cue:self.cue=d;self.pending=0;self.active=0;self.candidate=0;self.count=0
    def step(self,scores):
        p=np.asarray(scores,dtype=float)
        if p.shape!=(5,) or not np.isfinite(p).all():self.reset();raise ValueError('Finite five-class navigation evidence required')
        if self.pending:
            d=self.pending
            self.pending_count=self.pending_count+1 if int(p.argmax())==d and p[d]>=.85 else 0
            if self.pending_count>=3:self.cue=d;self.pending=0;self.pending_count=0
        thresholds=np.full(5,.85);thresholds[0]=.75
        if self.cue:thresholds[0]=.90;thresholds[self.cue]=.60
        d=int(p.argmax());required=thresholds[d]-(.05 if d==self.active and d else 0.)
        strong=d>0 and p[d]>=required and p[d]>p[0]+.05
        if strong:
            self.weak=0
            if d==self.active:self.candidate=0;self.count=0
            else:
                self.count=self.count+1 if self.candidate==d else 1;self.candidate=d;self.active=0
                if self.count>=3:self.active=d;self.count=0;self.candidate=0
        else:
            self.count=0;self.candidate=0;self.weak+=1
            if self.weak>=2:self.active=0
        status='ACTIVE' if self.active else 'REST' if p[0]>=thresholds[0] else 'UNKNOWN'
        return dict(direction=self.active,status=status,confirming=self.candidate,threshold=float(required),thresholds=thresholds.tolist(),cue_direction=self.cue,
            pending_direction=self.pending,auxiliary='RETURN_OR_SWITCH' if self.pending else 'CUE_MEMORY' if self.cue else 'UNKNOWN',policy='bounded_eye_memory_navigation_v1')

class NavEvidence:
    def __init__(self,backbone,weights,device,encoder_weights=None):
        import torch
        from scipy.signal import butter
        from .stream_jepa import Branch
        from .joint_stream import JointPreprocessor
        self.device=device;self.weights=np.asarray(weights,dtype='float32').reshape(32,1);self.indices=np.flatnonzero(self.weights[:,0]);self.net=Branch(32).to(device)
        self.encoder_weights=self.weights if encoder_weights is None else np.asarray(encoder_weights,dtype='float32').reshape(32,1)
        self.net.load_state_dict({k.removeprefix('net__motor.'):torch.as_tensor(v.copy(),device=device) for k,v in backbone.items() if k.startswith('net__motor.')});self.net.eval()
        self.mean=backbone['input_mean'].reshape(32,1);self.scale=backbone['input_scale'].reshape(32,1);self.filter=JointPreprocessor();self.state=None;self.samples=0
        self.sos=[butter(4,b,fs=250,btype='bandpass',output='sos') for b in [[8,13],[13,30]]];self.zi=[np.zeros((len(s),32,2)) for s in self.sos];self.history=np.empty((2,32,0))
    def step(self,x):
        import torch
        from scipy.signal import sosfilt
        z=(self.filter.step(x)-self.mean)/self.scale*self.encoder_weights
        with torch.inference_mode():h,self.state=self.net.step(torch.as_tensor(z[None],device=self.device,dtype=torch.float32),self.state)
        bands=[]
        for i,s in enumerate(self.sos):v,self.zi[i]=sosfilt(s,np.asarray(x,dtype=float),zi=self.zi[i],axis=-1);bands.append(v)
        self.history=np.concatenate((self.history,np.asarray(bands)),axis=-1)[:,:,-1500:];self.samples+=x.shape[1]
        values=[]
        for n in [125,375,750,1500]:values.append(np.log(np.maximum(np.mean(self.history[:,self.indices,-n:]**2,axis=-1),1e-8)).ravel())
        v=self.history[:,self.indices,-750:].sum(0);v=v-v.mean(1,keepdims=True);cov=v@v.T/max(1,v.shape[1]-1);den=np.sqrt(np.maximum(np.diag(cov),1e-8));corr=cov/den[:,None]/den[None,:]
        values.append(corr[np.triu_indices(len(self.indices),1)])
        return np.concatenate(values).astype('float32'),h[0].cpu().numpy(),self.samples>=375

def readout_predict(x,arrays,prefix):
    z=(np.asarray(x)-arrays[prefix+'mean'])/arrays[prefix+'scale'];logits=z@arrays[prefix+'weight'].T+arrays[prefix+'bias'];logits-=logits.max(axis=-1,keepdims=True);v=np.exp(logits);return v/v.sum(axis=-1,keepdims=True)

def feature_view(x,nchannels,mode):
    x=np.asarray(x).copy()
    if mode=='spatial_relative':
        v=x[...,:8*nchannels].reshape(x.shape[:-1]+(4,2,nchannels));v-=v.mean(-1,keepdims=True)
    elif mode!='absolute':raise ValueError('Unknown spectral feature view')
    return x

class HybridNavigationModel:
    def __init__(self,arrays,details,device=None):
        import torch
        from .ocular_event_training import build_model
        self.arrays=arrays;self.details=details;self.device=device or ('cuda:0' if torch.cuda.is_available() else 'cpu');self.algorithm=ALGORITHM;self.channels=CHANNELS;self.fs=250.;self.window_s=1.5;self.classes=np.arange(5)
        self.backbone={k.removeprefix('backbone__'):v for k,v in arrays.items() if k.startswith('backbone__')};self.eye_arrays={k.removeprefix('ocular__'):v for k,v in arrays.items() if k.startswith('ocular__')}
        self.eye=build_model(input_weights=self.eye_arrays['net__input_weights'].reshape(32)).to(self.device)
        self.eye.load_state_dict({k[5:]:torch.as_tensor(v.copy(),device=self.device) for k,v in self.eye_arrays.items() if k.startswith('net__')});self.eye.eval()
    def new_session(self):return HybridSession(self)
    def predict(self,x):
        p=np.asarray([self.new_session().step(v)['probabilities'] for v in x]);return dict(probabilities=p,labels=p.argmax(1),accepted=np.ones(len(p),bool),score_semantics='uncalibrated_hybrid_research_scores')

class HybridSession:
    def __init__(self,model):
        self.model=model;self.nav=NavEvidence(model.backbone,model.arrays['navigation_input_weights'],model.device,model.arrays.get('encoder_input_weights'));self.latch=EventLatch();self.policy=GazePolicy();self.pending=np.empty((32,0),dtype='float32');self.raw_history=np.empty((32,0),dtype='float32');self.received=0;self.last=None
    def step(self,x,disable_eye=False):
        x=np.asarray(x,dtype='float32')
        if x.ndim!=2 or x.shape[0]!=32 or not x.shape[1] or not np.isfinite(x).all():self.__init__(self.model);raise ValueError('Invalid stream; hybrid evidence reset')
        self.received+=x.shape[1];self.pending=np.concatenate((self.pending,x),1);updates=0
        try:
            while self.pending.shape[1]>=25:
                hop=self.pending[:,:25];self.pending=self.pending[:,25:];self.last=self._hop(hop,disable_eye);updates+=not self.last['warming']
        except Exception:self.__init__(self.model);raise
        result=copy.deepcopy(self.last) if self.last else dict(probabilities=[.2]*5,motor_scores=[.2]*5,eye_scores=[.2]*5,quality=dict(electrical_valid=True,contact_quality_known=False),navigation_decision=dict(direction=0,status='UNKNOWN'),warming=True,history_samples=0)
        result.update(new_prediction_updates=int(updates),pending_samples=self.pending.shape[1],received_history_samples=self.received);return result
    def _hop(self,hop,disable_eye):
        import torch
        from .quality import inspect_signal
        from .ocular_event_training import centered
        self.raw_history=np.concatenate((self.raw_history,hop),1)[:,-500:]
        q=inspect_signal(self.raw_history[:,-375:],CHANNELS,250,ELECTRICAL) if self.raw_history.shape[1]>=375 else dict(electrical_valid=True,prewarm=True,contact_quality_known=False)
        if not q['electrical_valid']:raise ValueError('Electrical quality rejected; hybrid state reset')
        f,h,ready=self.nav.step(hop);a=self.model.arrays
        f=feature_view(f,len(self.nav.indices),self.model.details.get('feature_mode','absolute'))
        p=(1-float(a['latent_gain']))*readout_predict(f,a,'spectral__')+float(a['latent_gain'])*readout_predict(h,a,'latent__')
        eye_x=(centered(self.raw_history)-self.model.eye_arrays['input_mean'])/self.model.eye_arrays['input_scale']
        with torch.inference_mode():eye=self.model.eye(torch.as_tensor(eye_x[None],device=self.model.device,dtype=torch.float32),[eye_x.shape[1]]).softmax(-1)[0].cpu().numpy()
        event=0 if disable_eye else self.latch.step(eye)
        if event:self.policy.eye_event(event)
        decision=self.policy.step(p) if ready else dict(direction=0,status='UNKNOWN',cue_direction=self.policy.cue,thresholds=[.85]*5,auxiliary='WARMUP')
        return dict(probabilities=p.tolist(),motor_scores=p.tolist(),eye_scores=eye.tolist(),navigation_decision=decision,eye_event=event,auxiliary_status=decision['auxiliary'],
            auxiliary_truth='manual_event_head_uncalibrated_not_objective_gaze',warming=not ready,history_samples=self.nav.samples,quality=q,
            evidence_semantics='causal_bandpower_covariance_and_frozen_JEPA_GRU',score_semantics='uncalibrated_joint_navigation_research_scores')
