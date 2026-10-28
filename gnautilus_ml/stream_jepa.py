"""Small causal recurrent JEPA. Persistent state is explicit, never a global cache."""
import copy
import numpy as np
import torch
from torch import nn
from scipy.signal import butter, sosfilt
from .navigation_contract import CHANNELS, MOTOR, FRONTAL, DEFAULT_POLICY, RUNTIME, ALGORITHM


def detach_state(value):
    if isinstance(value,torch.Tensor): return value.detach()
    if isinstance(value,dict): return {k:detach_state(v) for k,v in value.items()}
    return value


class Branch(nn.Module):
    def __init__(self,channels):
        super().__init__()
        self.temporal=nn.Conv2d(1,8,(1,17))
        self.spatial=nn.Conv2d(8,16,(channels,1),groups=8)
        self.gru=nn.GRU(16,32,batch_first=True)

    def step(self,x,state=None):
        # x B,C,T. Retain both convolution support and incomplete pooling groups.
        state=state or {};b,c,_=x.shape
        cache=state.get('cache',x.new_zeros(b,c,16))
        xx=torch.cat((cache,x),-1)
        z=torch.nn.functional.elu(self.spatial(self.temporal(xx[:,None]))).squeeze(2)
        tail=state.get('tail')
        if tail is not None:z=torch.cat((tail,z),-1)
        n=z.shape[-1]//4*4;h=state.get('hidden')
        if n:
            pooled=z[...,:n].reshape(b,16,-1,4).mean(-1).transpose(1,2)
            _,h=self.gru(pooled,h)
        out=h[-1] if h is not None else x.new_zeros(b,32)
        return out,dict(cache=xx[...,-16:],tail=z[...,n:],hidden=h)

    def batch(self,x,lengths):
        """Right padding is excluded by packed lengths, never treated as history."""
        z=torch.nn.functional.elu(self.spatial(self.temporal(torch.nn.functional.pad(x[:,None],(16,0))))).squeeze(2)
        n=z.shape[-1]//4*4;z=z[...,:n].reshape(len(x),16,-1,4).mean(-1).transpose(1,2)
        packed=nn.utils.rnn.pack_padded_sequence(z,[n//4 for n in lengths],batch_first=True,enforce_sorted=False)
        _,h=self.gru(packed);return h[-1]

    def sequence(self,x,state=None):
        state=state or {};b,c,_=x.shape;cache=state.get('cache',x.new_zeros(b,c,16));xx=torch.cat((cache,x),-1)
        z=torch.nn.functional.elu(self.spatial(self.temporal(xx[:,None]))).squeeze(2)
        if state.get('tail') is not None:z=torch.cat((state['tail'],z),-1)
        n=z.shape[-1]//4*4;h=state.get('hidden');pending=z[...,n:]
        if n:
            z,h=self.gru(z[...,:n].reshape(b,16,-1,4).mean(-1).transpose(1,2),h)
        else:z=x.new_empty(b,0,32)
        return z,dict(cache=xx[...,-16:],tail=pending,hidden=h)


class CausalJEPA(nn.Module):
    def __init__(self):
        super().__init__();self.motor=Branch(24);self.eye=Branch(8)
        self.motor_head=nn.Linear(32,5);self.eye_head=nn.Linear(32,5)
        # Native EOG logits retain BLINK/H-/H+/V-/V+ semantics, not navigation.
        self.native_eye_head=nn.Linear(32,5)
        self.predictors=nn.ModuleDict({k:nn.Sequential(nn.Linear(33,64),nn.GELU(),nn.Linear(64,32)) for k in ['motor','eye']})

    def step(self,x,state=None,gain=.25):
        state=state or {}
        m,ms=self.motor.step(x[:,8:],state.get('motor'))
        e,es=self.eye.step(x[:,:8],state.get('eye'))
        ml=self.motor_head(m);el=self.eye_head(e)
        # Auxiliary evidence is bounded; it does not replace the motor evidence gate.
        aux=torch.tanh(el[:,1:]-el[:,0:1])
        logits=ml+torch.cat((torch.zeros_like(el[:,:1]),float(gain)*aux),1)
        return dict(logits=logits,motor_logits=ml,eye_logits=el,
                    native_eye_logits=self.native_eye_head(e),motor=m,eye=e),dict(motor=ms,eye=es)

    def forward(self,x):return self.step(x)[0]

    def batch(self,x,lengths,gain=.25):
        m=self.motor.batch(x[:,8:],lengths);e=self.eye.batch(x[:,:8],lengths)
        ml=self.motor_head(m);el=self.eye_head(e);aux=torch.tanh(el[:,1:]-el[:,0:1])
        return dict(logits=ml+torch.cat((torch.zeros_like(el[:,:1]),float(gain)*aux),1),motor_logits=ml,eye_logits=el)

    def sequence(self,x,state=None,gain=.25):
        state=state or {};m,ms=self.motor.sequence(x[:,8:],state.get('motor'));e,es=self.eye.sequence(x[:,:8],state.get('eye'))
        ml=self.motor_head(m);el=self.eye_head(e);aux=torch.tanh(el[...,1:]-el[...,:1])
        return dict(logits=ml+torch.cat((torch.zeros_like(el[...,:1]),float(gain)*aux),-1),motor_logits=ml,eye_logits=el),dict(motor=ms,eye=es)


def ema_update(target,student,momentum=.996):
    with torch.no_grad():
        for tp,sp in zip(target.parameters(),student.parameters()):tp.lerp_(sp,1-momentum)


def jepa_loss(student,target,context,futures):
    """Targets are separate raw supports, never overlapping context windows."""
    context_out,_=student.step(context);total=context.new_tensor(0.);parts={}
    for branch in ['motor','eye']:
        z=context_out[branch];loss=context.new_tensor(0.)
        for horizon,future in enumerate(futures):
            with torch.no_grad():t=target(future)[branch]
            code=z.new_full((len(z),1),float(horizon))
            p=student.predictors[branch](torch.cat((z,code),1))
            loss=loss+torch.nn.functional.smooth_l1_loss(torch.nn.functional.layer_norm(p,(32,)),torch.nn.functional.layer_norm(t,(32,)))
        std=torch.sqrt(z.var(0,unbiased=False)+1e-4)
        centered=z-z.mean(0);cov=centered.T@centered/max(1,len(z)-1)
        off=cov-torch.diag(torch.diagonal(cov))
        reg=.1*torch.relu(1-std).mean()+.01*off.square().mean()
        parts[branch]=loss/len(futures)+reg;total=total+parts[branch]
    return total,parts


def transfer_branch(branch,arrays,source_channels,destination_channels,copy_spatial=True):
    """Copy actual pretrained parameters; unobserved destination columns start at zero."""
    state={k.removeprefix('net__'):torch.as_tensor(v.copy()) for k,v in arrays.items() if k.startswith('net__')}
    copied=[]
    for name,module in [('temporal',branch.temporal),('gru',branch.gru)]:
        module.load_state_dict({k.split('.',1)[1]:v for k,v in state.items() if k.startswith(name+'.')});copied.append(name)
    if copy_spatial:
        if not set(source_channels)<=set(destination_channels):raise ValueError('Missing pretrained positions cannot be invented')
        w=torch.zeros_like(branch.spatial.weight)
        for i,name in enumerate(source_channels):w[:,:,destination_channels.index(name),:]=state['spatial.weight'][:,:,i,:]
        branch.spatial.load_state_dict({'weight':w,'bias':state['spatial.bias']});copied.append('spatial_by_position')
    return copied


class Preprocessor:
    """Zero-initialized causal IIR state, common to training/replay/live."""
    def __init__(self):
        self.sos=[butter(4,[.5,8],fs=250,btype='bandpass',output='sos'),
                  butter(4,[8,30],fs=250,btype='bandpass',output='sos')];self.reset()
    def reset(self):self.zi=[np.zeros((len(s),c,2)) for s,c in zip(self.sos,[8,24])]
    def step(self,x):
        x=np.asarray(x,dtype=np.float64)
        if x.ndim!=2 or x.shape[0]!=32 or not np.isfinite(x).all():raise ValueError('32 finite physical EEG columns required')
        out=[]
        for i,a in enumerate([x[:8],x[8:]]):
            y,self.zi[i]=sosfilt(self.sos[i],a,zi=self.zi[i],axis=-1);out.append(y)
        return np.concatenate(out).astype('float32')


class NavigationDecoder:
    def __init__(self,policy=None):
        self.policy=dict(DEFAULT_POLICY,**(policy or {}));self.reset()
        p=self.policy
        if not .5<=p['aligned_threshold']<p['conservative_threshold']<=.995 or not 0<=p['auxiliary_gain']<=.5 or not 0<=p['hold_discount']<=.15:raise ValueError('Unsafe navigation decision policy')
        if p['confirm_updates']<3 or p['stop_updates']<1:raise ValueError('Insufficient causal confirmation')
    def reset(self):self.active=0;self.candidate=0;self.count=0;self.weak=0
    def step(self,motor,scores,auxiliary='UNKNOWN',aux_direction=0):
        p=self.policy;winner=int(np.argmax(scores));aligned=auxiliary=='ALIGNED' and aux_direction==winner and winner>0
        threshold=p['aligned_threshold'] if aligned else p['conservative_threshold']
        # A return never provides a fresh opposite-direction cue.
        if auxiliary=='RETURN':winner=self.active or 0
        required=threshold-(p['hold_discount'] if winner==self.active and self.active else 0)
        strong=winner>0 and motor[winner]>=required and motor[winner]>motor[0]
        if strong:
            self.weak=0
            if winner==self.active:self.candidate=0;self.count=0
            else:
                if self.candidate==winner:self.count+=1
                else:self.candidate=winner;self.count=1
                # Do not continue the old direction while confirming a switch.
                self.active=0
                if self.count>=p['confirm_updates']:self.active=winner;self.candidate=0;self.count=0
        else:
            self.candidate=0;self.count=0;self.weak+=1
            if self.weak>=p['stop_updates']:self.active=0
        status='ACTIVE' if self.active else 'REST' if motor[0]>=p['conservative_threshold'] and int(np.argmax(scores))==0 else 'UNKNOWN'
        return dict(direction=self.active,status=status,confirming=self.candidate,threshold=float(required),
                    auxiliary=auxiliary,policy='motor_evidence_with_bounded_gaze_v1')


class StreamModel:
    def __init__(self,arrays,details,device=None):
        self.algorithm=ALGORITHM;self.channels=list(CHANNELS);self.fs=250.;self.window_s=1.5;self.classes=np.arange(5)
        self.arrays=arrays;self.details=details
        self.device=device or ('cuda:0' if torch.cuda.is_available() else 'cpu')
        self.net=CausalJEPA().to(self.device)
        self.net.load_state_dict({k[5:]:torch.as_tensor(v.copy(),device=self.device) for k,v in arrays.items() if k.startswith('net__')});self.net.eval()
        self.mean=arrays['input_mean'].reshape(32,1);self.scale=arrays['input_scale'].reshape(32,1)
        if np.any(self.scale<=0):raise ValueError('Invalid normalization scale')
    def new_session(self):return StreamSession(self)
    def predict(self,x):
        rows=[]
        for value in x:
            s=self.new_session();rows.append(s.step(value)['probabilities'])
        p=np.asarray(rows);return dict(probabilities=p,labels=p.argmax(1),accepted=np.ones(len(p),bool),score_semantics='uncalibrated_joint_navigation_scores')


class StreamSession:
    def __init__(self,model):
        self.model=model;self.filter=Preprocessor();self.state=None;self.samples=0;self.eye_direction=0;self.eye_count=0;self.return_count=0
        self.decoder=NavigationDecoder(model.details.get('decision_policy'));self.raw_history=np.empty((32,0),dtype='float32')
    def step(self,x,disable_eye=False):
        x=np.asarray(x,dtype='float32');self.raw_history=np.concatenate((self.raw_history,x),1)[:,-375:]
        z=(self.filter.step(x)-self.model.mean)/self.model.scale
        with torch.inference_mode():
            out,self.state=self.model.net.step(torch.as_tensor(z[None],device=self.model.device),self.state,
                gain=0. if disable_eye else self.decoder.policy['auxiliary_gain'])
            temperature=self.decoder.policy['temperature']
            scores={k:torch.softmax(out[k]/temperature,-1)[0].cpu().numpy() for k in ['logits','motor_logits','eye_logits','native_eye_logits']}
        self.samples+=x.shape[-1];eye=scores['eye_logits'];d=int(eye.argmax());aux='UNKNOWN'
        # These are transferred/weak estimates, never objective measured eye positions.
        blink=float(scores['native_eye_logits'][0])
        opposite={1:2,2:1,3:4,4:3}
        if not disable_eye:
            if blink>=.8:aux='BLINK'
            elif self.eye_direction and d==0 and eye[0]>=.7:aux='CENTER';self.return_count=0
            elif self.eye_direction and d==opposite[self.eye_direction] and eye[d]>=.7:
                self.return_count+=1
                # Suppress the transient; sustained new motor evidence may later
                # confirm a deliberate opposite command under the higher gate.
                new_motor=int(scores['motor_logits'].argmax())
                if self.return_count<=3 or new_motor!=d or scores['motor_logits'][d]<self.decoder.policy['conservative_threshold']:aux='RETURN'
            elif d and eye[d]>=.7:
                if d==self.eye_direction:self.eye_count+=1
                else:self.eye_direction=d;self.eye_count=1
                if self.eye_count>=3:aux='ALIGNED'
        decision=self.decoder.step(scores['motor_logits'],scores['logits'],aux,d)
        if self.return_count>3 and decision['direction']==d and d:
            self.eye_direction=d;self.eye_count=3;self.return_count=0
        return dict(probabilities=scores['logits'].tolist(),motor_scores=scores['motor_logits'].tolist(),eye_scores=eye.tolist(),
                    auxiliary_status=aux,auxiliary_truth='transferred_weak_estimate_not_eye_tracker',blink_score=blink,
                    navigation_decision=decision,warming=self.samples<375,history_samples=self.samples,
                    score_semantics='joint_navigation_scores_not_intention_probability')


def arrays_from(net,mean,scale):
    return dict(input_mean=np.asarray(mean,dtype='float32').reshape(32),input_scale=np.asarray(scale,dtype='float32').reshape(32),
                **{'net__'+k:v.detach().cpu().numpy() for k,v in net.state_dict().items()})
