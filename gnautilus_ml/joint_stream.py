"""Full physical montage, causal spatial integration and feature-level gaze fusion.

The internal ``motor`` parameter name preserves native teacher interfaces. In
this version that encoder receives ALL 32 channels, including frontal EEG.
"""
import copy
import numpy as np
import torch
from torch import nn
from scipy.signal import butter,sosfilt
from .stream_jepa import Branch,CausalJEPA,StreamModel,NavigationDecoder
from .navigation_contract import CHANNELS,ALGORITHM,DEFAULT_POLICY,ELECTRICAL

RUNTIME='gnautilus.causal_jepa_stream.v2'


class JointJEPA(CausalJEPA):
    def __init__(self):
        super().__init__();self.motor=Branch(32)
        self.fusion=nn.Sequential(nn.Linear(64,32),nn.GELU(),nn.LayerNorm(32))
        self.joint_head=nn.Linear(32,5)
        self.predictors['joint']=nn.Sequential(nn.Linear(33,64),nn.GELU(),nn.Linear(64,32))

    def combine(self,m,e,disable_eye=False):
        j=self.fusion(torch.cat((m,torch.zeros_like(e) if disable_eye else e),-1))
        return dict(logits=self.joint_head(j),motor_logits=self.motor_head(m),eye_logits=self.eye_head(e),
                    native_eye_logits=self.native_eye_head(e),motor=m,eye=e,joint=j)

    def step(self,x,state=None,gain=None,disable_eye=False):
        state=state or {};m,ms=self.motor.step(x,state.get('motor'));e,es=self.eye.step(x[:,:8],state.get('eye'))
        return self.combine(m,e,disable_eye),dict(motor=ms,eye=es)

    def sequence(self,x,state=None,gain=None,disable_eye=False):
        state=state or {};m,ms=self.motor.sequence(x,state.get('motor'));e,es=self.eye.sequence(x[:,:8],state.get('eye'))
        return self.combine(m,e,disable_eye),dict(motor=ms,eye=es)

    def batch(self,x,lengths,gain=None,disable_eye=False):
        return self.combine(self.motor.batch(x,lengths),self.eye.batch(x[:,:8],lengths),disable_eye)


class JointPreprocessor:
    """Identical causal .5-30Hz bandwidth for every physical input column."""
    def __init__(self):
        self.sos=butter(4,[.5,30],fs=250,btype='bandpass',output='sos');self.reset()
    def reset(self):self.zi=np.zeros((len(self.sos),32,2))
    def step(self,x):
        x=np.asarray(x,dtype=np.float64)
        if x.ndim!=2 or x.shape[0]!=32 or not np.isfinite(x).all():raise ValueError('32 finite physical EEG columns required')
        out,self.zi=sosfilt(self.sos,x,zi=self.zi,axis=-1)
        return out.astype('float32')


def joint_jepa_loss(student,target,context,futures):
    out=student(context);total=context.new_tensor(0.);parts={}
    for name in ['motor','eye','joint']:
        z=out[name];loss=context.new_tensor(0.)
        for horizon,future in enumerate(futures):
            with torch.no_grad():t=target(future)[name]
            p=student.predictors[name](torch.cat((z,z.new_full((len(z),1),float(horizon))),1))
            loss=loss+torch.nn.functional.smooth_l1_loss(torch.nn.functional.layer_norm(p,(32,)),torch.nn.functional.layer_norm(t,(32,)))
        centered=z-z.mean(0);cov=centered.T@centered/max(1,len(z)-1)
        std=torch.sqrt(z.var(0,unbiased=False)+1e-4)
        reg=.1*torch.relu(1-std).mean()+.01*(cov-torch.diag(torch.diagonal(cov))).square().mean()
        parts[name]=loss/len(futures)+reg
        total=total+parts[name]*(.25 if name=='eye' else .5)
    return total,parts


class JointDecoder(NavigationDecoder):
    def step(self,shared_probe,scores,auxiliary='UNKNOWN',aux_direction=0):
        # Only the learned joint head controls the gate. A standalone eye head
        # cannot manufacture an activation; it only selects a conservative gate.
        out=super().step(scores,scores,auxiliary,aux_direction)
        out['policy']='shared_full32_joint_v2'
        return out


class JointStreamModel(StreamModel):
    def __init__(self,arrays,details,device=None):
        self.algorithm=ALGORITHM;self.channels=list(CHANNELS);self.fs=250.;self.window_s=1.5;self.classes=np.arange(5)
        self.arrays=arrays;self.details=details;self.electrical_policy=ELECTRICAL;self.device=device or ('cuda:0' if torch.cuda.is_available() else 'cpu')
        self.net=JointJEPA().to(self.device)
        self.net.load_state_dict({k[5:]:torch.as_tensor(v.copy(),device=self.device) for k,v in arrays.items() if k.startswith('net__')});self.net.eval()
        self.mean=arrays['input_mean'].reshape(32,1);self.scale=arrays['input_scale'].reshape(32,1)
        if np.any(self.scale<=0):raise ValueError('Invalid normalization scale')
    def new_session(self):return JointSession(self)


class JointSession:
    def __init__(self,model):
        self.model=model;self.filter=JointPreprocessor();self.state=None;self.samples=0
        self.eye_direction=0;self.eye_count=0;self.return_count=0
        self.decoder=JointDecoder(model.details.get('decision_policy'));self.raw_history=np.empty((32,0),dtype='float32')
        self.pending=np.empty((32,0),dtype='float32');self.received=0;self.last_result=None

    def step(self,x,disable_eye=False):
        x=np.asarray(x,dtype='float32')
        if x.ndim!=2 or x.shape[0]!=32 or not x.shape[1] or not np.isfinite(x).all():
            self.__init__(self.model);raise ValueError('Invalid stream samples; state reset')
        self.received+=x.shape[1];self.pending=np.concatenate((self.pending,x),1);updates=0
        try:
            while self.pending.shape[1]>=25:
                hop=self.pending[:,:25];self.pending=self.pending[:,25:]
                self.last_result=self._hop(hop,disable_eye)
                updates+=not self.last_result['warming']
        except Exception:
            self.__init__(self.model);raise
        result=copy.deepcopy(self.last_result) if self.last_result is not None else dict(probabilities=[.2]*5,motor_scores=[.2]*5,eye_scores=[.2]*5,
            navigation_decision=dict(direction=0,status='UNKNOWN',confirming=0,threshold=self.decoder.policy['conservative_threshold'],auxiliary='UNKNOWN',policy='shared_full32_joint_v2'),
            auxiliary_status='UNKNOWN',warming=True,history_samples=0,quality=dict(electrical_valid=True,prewarm=True,contact_quality_known=False))
        result.update(new_prediction_updates=int(updates),pending_samples=self.pending.shape[1],received_history_samples=self.received)
        return result

    def _hop(self,x,disable_eye):
        from .quality import inspect_signal
        self.raw_history=np.concatenate((self.raw_history,x),1)[:,-375:]
        quality=inspect_signal(self.raw_history,CHANNELS,250,self.model.electrical_policy) if self.raw_history.shape[1]>=375 else dict(electrical_valid=True,prewarm=True,contact_quality_known=False)
        if not quality['electrical_valid']:raise ValueError('Electrical quality rejected at fixed sample hop; state reset')
        z=(self.filter.step(x)-self.model.mean)/self.model.scale
        with torch.inference_mode():
            out,self.state=self.model.net.step(torch.as_tensor(z[None],device=self.model.device),self.state,disable_eye=disable_eye)
            # Calibration belongs to the joint task only, not transferred EOG.
            scores={k:torch.softmax(out[k]/(self.decoder.policy['temperature'] if k=='logits' else 1.),-1)[0].cpu().numpy()
                    for k in ['logits','motor_logits','eye_logits','native_eye_logits']}
        self.samples+=x.shape[-1];eye=scores['eye_logits'];d=int(eye.argmax());aux='UNKNOWN';blink=float(scores['native_eye_logits'][0])
        if self.samples>=375 and not disable_eye:
            if blink>=.9:aux='BLINK'
            elif self.eye_direction and d==0 and eye[0]>=.7:aux='CENTER';self.return_count=0
            elif self.eye_direction and d=={1:2,2:1,3:4,4:3}[self.eye_direction] and eye[d]>=.7:
                self.return_count+=1
                if self.return_count<=3 or int(scores['logits'].argmax())!=d or scores['logits'][d]<self.decoder.policy['conservative_threshold']:aux='RETURN'
            elif d and eye[d]>=.7:
                self.eye_count=self.eye_count+1 if d==self.eye_direction else 1;self.eye_direction=d
                if self.eye_count>=3:aux='ALIGNED'
        decision=self.decoder.step(scores['motor_logits'],scores['logits'],aux,d) if self.samples>=375 else dict(direction=0,status='UNKNOWN',confirming=0,
            threshold=self.decoder.policy['conservative_threshold'],auxiliary='UNKNOWN',policy='shared_full32_joint_v2')
        if self.return_count>3 and decision['direction']==d and d:self.eye_direction=d;self.eye_count=3;self.return_count=0
        return dict(probabilities=scores['logits'].tolist(),motor_scores=scores['motor_logits'].tolist(),eye_scores=eye.tolist(),
                    evidence_semantics='shared_full32_probe_and_frontal_auxiliary',auxiliary_status=aux,
                    auxiliary_truth='transferred_weak_estimate_not_eye_tracker',blink_score=blink,navigation_decision=decision,
                    warming=self.samples<375,history_samples=self.samples,quality=quality,score_semantics='joint_navigation_scores_not_intention_probability')
