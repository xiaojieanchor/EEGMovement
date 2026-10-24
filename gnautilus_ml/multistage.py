"""Numeric finite-history feature pipelines with train-only model selection.

No deserialized executable objects.  Development inputs are never passed here.
The held-out predictions used for calibration are produced by refitting every
spatial filter and scaler inside the respective training fold.
"""
import numpy as np
from scipy.linalg import eigh
from scipy.signal import butter, sosfilt, sosfilt_zi

METHODS={
    'filterbank_lda':'多频带CSP / 收缩LDA',
    'temporal_lda':'低频时域 / 收缩LDA',
    'covariance_lda':'协方差切空间 / 收缩LDA',
    'pipeline_ensemble':'训练组内选择 / 多层融合',
    'gaze_pipeline':'眼动低频与形态 / 训练组内选择',
}
BANDS=((4,8),(8,12),(12,16),(16,24),(24,32),(32,40))


def _spd_function(a, function, positive=True):
    value,vector=np.linalg.eigh(a)
    if positive:value=np.maximum(value,1e-10)
    return (vector*function(value)[...,None,:])@np.swapaxes(vector,-1,-2)


def _covariance(x):
    x=x-x.mean(-1,keepdims=True)
    cov=np.einsum('nct,ndt->ncd',x,x)
    cov/=np.maximum(np.trace(cov,axis1=1,axis2=2),1e-20)[:,None,None]
    c=cov.shape[-1]
    return .9*cov+.1*np.eye(c)[None]/c


def _softmax(logits,temperature=1.):
    logits=logits/temperature;logits-=logits.max(1,keepdims=True)
    p=np.exp(logits);return p/p.sum(1,keepdims=True)


def _balanced(y,p,classes):
    return float(np.mean([np.mean(p[y==c]==c) for c in classes]))


class NumericPipeline:
    def __init__(self,method,fs,car=False):
        self.method=method;self.fs=float(fs);self.car=bool(car);self.arrays={};self.classes=None

    def _input(self,x):
        x=np.asarray(x,dtype=np.float64)
        if self.car:x=x-x.mean(1,keepdims=True)
        return x

    def _filter(self,x,band):
        if band[1]>=self.fs/2:raise ValueError('Feature band exceeds the actual Nyquist frequency')
        # A trailing window is the complete declared history.  No filtfilt,
        # centered future padding, or cross-window hidden state is used.
        return sosfilt(butter(4,band,btype='bandpass',fs=self.fs,output='sos'),x,axis=-1)

    def features(self,x,fit_y=None):
        x=self._input(x)
        if self.method in ['gaze_shape_lda','gaze_level_lda']:
            if self.fs<=24:raise ValueError('Gaze lowpass needs sampling rate above 24Hz')
            if self.method=='gaze_shape_lda':x=x-x[:,:,:max(1,x.shape[-1]//10)].mean(-1,keepdims=True)
            sos=butter(4,12,btype='lowpass',fs=self.fs,output='sos')
            # Constant initialization avoids interpreting an arbitrary DC offset
            # as a filter startup saccade. No highpass removes held gaze levels.
            zi=sosfilt_zi(sos)[:,None,None,:]*x[:,:,0][None,:,:,None]
            z,_=sosfilt(sos,x,axis=-1,zi=zi)
            bins=np.array_split(np.arange(z.shape[-1]),min(12,z.shape[-1]))
            means=np.stack([z[:,:,ids].mean(-1) for ids in bins],axis=-1)
            return np.concatenate([means.reshape(len(x),-1),np.diff(means,axis=-1).reshape(len(x),-1),
                                   np.ptp(z,axis=-1),np.sqrt(np.mean(np.diff(z,axis=-1)**2,axis=-1))],axis=1)
        if self.method=='filterbank_lda':
            parts=[]
            for index,band in enumerate(BANDS):
                z=self._filter(x,band);key='spatial_'+str(index)
                if fit_y is not None:
                    cov=_covariance(z);filters=[]
                    labels=self.classes[:1] if len(self.classes)==2 else self.classes
                    for label in labels:
                        a=cov[fit_y==label].mean(0);b=cov[fit_y!=label].mean(0)
                        _,v=eigh(a,a+b);k=min(4,z.shape[1]);take=list(range(k//2))+list(range(z.shape[1]-(k-k//2),z.shape[1]))
                        filters.append(v[:,take].T)
                    self.arrays[key]=np.concatenate(filters)
                power=np.var(np.einsum('kc,nct->nkt',self.arrays[key],z),axis=-1)
                parts.append(np.log10(np.maximum(power/np.maximum(power.sum(1,keepdims=True),1e-20),1e-20)))
            features=np.concatenate(parts,axis=1)
            if fit_y is not None:
                overall=features.mean(0);between=sum(np.sum(fit_y==label)*(features[fit_y==label].mean(0)-overall)**2 for label in self.classes)
                within=sum(np.sum((features[fit_y==label]-features[fit_y==label].mean(0))**2,axis=0) for label in self.classes)
                self.arrays['feature_indices']=np.argsort(-(between/np.maximum(within,1e-12)),kind='stable')[:min(32,features.shape[1])]
            return features[:,self.arrays['feature_indices']]
        if self.method=='temporal_lda':
            # Signed low-frequency observations are intentional.  This branch
            # may contain ocular/visual cue information; it is not called pure MI.
            baseline=x[:,:,:max(1,x.shape[-1]//10)].mean(-1,keepdims=True)
            z=self._filter(x-baseline,(.5,8))
            blocks=np.array_split(np.arange(z.shape[-1]),min(12,z.shape[-1]),axis=0)
            means=np.stack([z[:,:,at].mean(-1) for at in blocks],axis=-1)
            return np.concatenate([means.reshape(len(x),-1),np.diff(means,axis=-1).reshape(len(x),-1)],axis=1)
        if self.method=='covariance_lda':
            cov=_covariance(self._filter(x,(8,30)))
            if fit_y is not None:
                reference=_spd_function(np.mean(_spd_function(cov,np.log),axis=0),np.exp,positive=False)
                self.arrays['reference_inverse_sqrt']=_spd_function(reference,lambda a:1/np.sqrt(a))
            ref=self.arrays['reference_inverse_sqrt'];mapped=_spd_function(ref@cov@ref,np.log)
            i,j=np.triu_indices(mapped.shape[-1]);return mapped[:,i,j]*np.where(i==j,1.,np.sqrt(2.))
        raise ValueError('Unknown numeric feature method')

    def fit(self,x,y):
        self.classes=np.unique(y);f=self.features(x,fit_y=y)
        self.arrays['mean']=f.mean(0);self.arrays['scale']=np.maximum(f.std(0),1e-7)
        f=(f-self.arrays['mean'])/self.arrays['scale']
        means=np.array([f[y==c].mean(0) for c in self.classes])
        residual=np.empty_like(f)
        for label,mean in zip(self.classes,means):residual[y==label]=f[y==label]-mean
        cov=residual.T@residual/max(1,len(y)-len(self.classes));size=len(cov)
        cov=.8*cov+.2*np.trace(cov)/max(1,size)*np.eye(size)+np.eye(size)*1e-7
        self.arrays['weight']=np.linalg.solve(cov,means.T)
        self.arrays['bias']=-.5*np.sum(means*self.arrays['weight'].T,axis=1)
        return self

    def logits(self,x):
        features=(self.features(x)-self.arrays['mean'])/self.arrays['scale']
        return features@self.arrays['weight']+self.arrays['bias']


def training_folds(y,groups):
    """Native-group folds; one-group fallback is purged chronological blocks.

    The fallback is inner model selection only. It never becomes a new outer
    session, source, test set, or wearing identity.
    """
    groups=np.asarray(groups) if groups is not None else np.repeat('one_training_source',len(y))
    classes=np.unique(y);native=np.unique(groups);folds=[]
    if len(native)>=2:
        for held in np.array_split(native,min(3,len(native))):
            valid=np.isin(groups,held);train=~valid
            if all(np.sum(y[train]==c)>=3 and np.sum(y[valid]==c)>=1 for c in classes):folds.append((train,valid))
        kind='native_training_groups'
    else:
        for ids in np.array_split(np.arange(len(y)),3):
            valid=np.zeros(len(y),bool);valid[ids]=True;train=~valid
            # Candidate trials are chronological, nonoverlapping windows. Purge
            # two neighboring trials on either side of each held block as well.
            train[max(0,int(ids[0])-2):min(len(y),int(ids[-1])+3)]=False
            if all(np.sum(y[train]==c)>=3 and np.sum(y[valid]==c)>=1 for c in classes):folds.append((train,valid))
        kind='purged_chronological_training_blocks_not_independent_sessions'
    if len(folds)<2:raise ValueError('Insufficient class/group support for train-only pipeline selection')
    return folds,kind


def _calibrate(logits,y,classes):
    target=np.searchsorted(classes,y);options=[]
    for temperature in [.5,1.,2.,4.,8.]:
        p=_softmax(logits.copy(),temperature)
        loss=float(-np.log(np.maximum(p[np.arange(len(y)),target],1e-12)).mean())
        options.append((loss,temperature))
    return min(options)[1]


def acceptance_policy(y,p,classes):
    """Training-OOF risk/coverage selection; no external validation labels."""
    label=classes[p.argmax(1)];confidence=p.max(1);margin=np.sort(p,axis=1)[:,-1]-np.sort(p,axis=1)[:,-2]
    candidates=[]
    for threshold in [.0,.4,.5,.6,.7,.8,.9]:
        for gap in [.0,.1,.2]:
            accepted=(confidence>=threshold)&(margin>=gap)
            coverage=float(accepted.mean());per={str(c):float(np.mean(accepted[y==c])) for c in classes}
            accuracy=float(np.mean(label[accepted]==y[accepted])) if accepted.any() else None
            if accuracy is not None and accuracy>=.85 and min(per.values())>=.5:candidates.append((coverage,accuracy,-threshold,-gap))
    if candidates:
        coverage,accuracy,threshold,gap=max(candidates)
        return dict(schema='gnautilus.oof_rejection.v1',threshold=-threshold,margin=-gap,target_met_on_training_oof=True,training_oof_coverage=coverage,training_oof_accepted_accuracy=accuracy,
                    semantics='Internal selection estimate; not independent acceptance or an action authorization')
    return dict(schema='gnautilus.oof_rejection.v1',threshold=0.,margin=0.,target_met_on_training_oof=False,
                semantics='No OOF threshold achieved 85% accuracy with at least 50% coverage per class; no high-precision claim')


def fit_multistage(model,x,y,groups,progress):
    analysis={}
    if model.algorithm=='gaze_pipeline':
        # Ocular reference datasets keep their actual electrode identities.
        # Personal scalp input uses an explicit frontal subset, not controller-
        # associated motor-area activity. Raw recordings retain every channel.
        frontal={'Fp1','Fp2','Fpz','AF3','AF4','AF7','AF8','AFz','F7','F8','F3','F4','Fz'}
        indices=list(range(len(model.channels))) if all(c.startswith('EOG_') for c in model.channels) else [i for i,c in enumerate(model.channels) if c in frontal]
        if len(indices)<2:raise ValueError('Gaze pipeline needs at least two identified frontal or native EOG channels')
        analysis=dict(analysis_channel_indices=indices,analysis_channels=[model.channels[i] for i in indices],feature_source='native_EOG' if all(c.startswith('EOG_') for c in model.channels) else 'frontal_scalp_ocular_observation')
        x=x[:,indices]
    if model.algorithm not in ['pipeline_ensemble','gaze_pipeline']:
        child=NumericPipeline(model.algorithm,model.fs).fit(x,y)
        model.arrays=child.arrays;model.details=dict(pipeline_schema='gnautilus.multistage.v1',method=model.algorithm,car=False,score_semantics='uncalibrated_lda_posterior')
        return model
    folds,kind=training_folds(y,groups);classes=np.unique(y)
    configs=[('gaze_shape_lda',False),('gaze_level_lda',False),('temporal_lda',False)] if model.algorithm=='gaze_pipeline' else [('filterbank_lda',False),('filterbank_lda',True),('covariance_lda',False),('temporal_lda',False)]
    predictions=[];summaries=[];supported=np.zeros(len(y),bool)
    for _,valid in folds:supported|=valid
    for index,(method,car) in enumerate(configs):
        logits=np.zeros((len(y),len(classes)));fold_scores=[]
        for fold,(train,valid) in enumerate(folds):
            child=NumericPipeline(method,model.fs,car).fit(x[train],y[train]);logits[valid]=child.logits(x[valid]);predicted=classes[logits[valid].argmax(1)]
            score=_balanced(y[valid],predicted,classes);fold_scores.append(score)
            if progress:progress(dict(stage='inner_fold_fit',candidate=index+1,candidates=len(configs),method=method,car=car,fold=fold+1,folds=len(folds),inner_balanced_accuracy=score,training_windows=int(train.sum()),held_training_windows=int(valid.sum()),external_development_used=False))
        temperature=_calibrate(logits[supported],y[supported],classes);p=_softmax(logits.copy(),temperature);predictions.append(p)
        summaries.append(dict(method=method,car=car,temperature=temperature,inner_fold_ba=fold_scores,inner_balanced_accuracy=_balanced(y[supported],classes[p[supported].argmax(1)],classes)))
    # Only a fixed, small set of equal-weight combinations is compared.  No
    # development predictions, labels, or scores enter selection or calibration.
    mixtures=[(i,) for i in range(len(configs))]+([(0,1),(0,2),(1,2),(0,1,2)] if model.algorithm=='gaze_pipeline' else [(0,1),(0,2),(0,3),(1,3),(0,2,3)])
    ranked=[]
    for mix in mixtures:
        p=np.mean([predictions[i][supported] for i in mix],axis=0);pred=classes[p.argmax(1)]
        recall=[float(np.mean(pred[y[supported]==c]==c)) for c in classes]
        ranked.append((float(np.mean(recall)),min(recall),-len(mix),mix))
    _,_,_,selected=max(ranked);oof=np.mean([predictions[i][supported] for i in selected],axis=0)
    model.arrays={};children=[]
    for number,index in enumerate(selected):
        method,car=configs[index];child=NumericPipeline(method,model.fs,car).fit(x,y);prefix='child'+str(number)+'__'
        model.arrays.update({prefix+k:v for k,v in child.arrays.items()})
        children.append(dict(prefix=prefix,method=method,car=car,temperature=summaries[index]['temperature']))
    model.details=dict(pipeline_schema='gnautilus.multistage.v1',children=children,selection='training_oof_only',fold_kind=kind,
        inner_candidates=summaries,selected_candidates=list(selected),inner_balanced_accuracy=_balanced(y[supported],classes[oof.argmax(1)],classes),
        training_oof_windows=int(supported.sum()),training_oof_total_windows=len(y),acceptance_policy=acceptance_policy(y[supported],oof,classes),
        score_semantics='training_oof_temperature_scaled_ensemble_not_speed')
    model.details.update(analysis)
    if progress:progress(dict(stage='pipeline_selected',selected_candidates=list(selected),inner_balanced_accuracy=model.details['inner_balanced_accuracy'],external_development_used=False))
    return model


def predict_multistage(model,x):
    if model.details.get('pipeline_schema')!='gnautilus.multistage.v1':raise ValueError('Unsupported numeric pipeline version')
    if model.algorithm=='gaze_pipeline' and 'analysis_channel_indices' in model.details:
        indices=model.details['analysis_channel_indices']
        if len(indices)<2 or len(set(indices))!=len(indices) or any(type(i) is not int or not 0<=i<x.shape[1] for i in indices):raise ValueError('Invalid gaze analysis channel contract')
        x=x[:,indices]
    if model.algorithm not in ['pipeline_ensemble','gaze_pipeline']:
        child=NumericPipeline(model.details['method'],model.fs,model.details['car']);child.arrays=model.arrays
        return _softmax(child.logits(x))
    rows=[]
    for config in model.details['children']:
        child=NumericPipeline(config['method'],model.fs,config['car']);prefix=config['prefix']
        child.arrays={k[len(prefix):]:v for k,v in model.arrays.items() if k.startswith(prefix)}
        rows.append(_softmax(child.logits(x),config['temperature']))
    if not rows:raise ValueError('Numeric ensemble contains no fitted branches')
    return np.mean(rows,axis=0)
