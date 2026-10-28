"""Class/trial-balanced shrinkage LDA, exported as an ordinary linear readout."""
from pathlib import Path
import numpy as np
from .common import atomic

def fit_closed_form(x,y,ids,shrinkage,out=None):
    from .governance import authorize_training
    from .intent_repair_training import balanced
    authorize_training();x=np.asarray(x,dtype=float);y=np.asarray(y);ids=np.asarray(ids);classes=np.unique(y)
    if not np.array_equal(classes,np.arange(len(classes))) or not 0<shrinkage<=1:raise ValueError('Contiguous classes and positive shrinkage required')
    w=balanced(y,ids);mean=(x*w[:,None]).sum(0);scale=np.sqrt(np.maximum(((x-mean)**2*w[:,None]).sum(0),1e-6));z=(x-mean)/scale;means=[];residual=np.empty_like(z)
    for c in classes:
        take=y==c;center=(z[take]*w[take,None]).sum(0)/w[take].sum();means.append(center);residual[take]=z[take]-center
    means=np.asarray(means);cov=(residual*w[:,None]).T@residual;cov=(1-shrinkage)*cov+shrinkage*np.diag(np.diag(cov))+np.eye(x.shape[1])*1e-4;weight=np.linalg.solve(cov,means.T).T;bias=-.5*np.sum(weight*means,axis=1)-np.log(len(classes));result=dict(mean=mean.astype('float32'),scale=scale.astype('float32'),weight=weight.astype('float32'),bias=bias.astype('float32'))
    if out is not None:
        out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);np.savez(out.with_suffix('.npz'),**result);atomic(out.with_suffix('.json'),dict(method='class_trial_balanced_shrinkage_LDA',shrinkage=shrinkage,source_trial_ids=list(map(str,np.unique(ids))),class_frame_support={str(c):int((y==c).sum()) for c in classes},closed_form_fit=True,gradient_updates=0))
    return result
