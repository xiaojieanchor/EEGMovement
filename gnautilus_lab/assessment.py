"""Group-aware evaluation primitives; no retrospective score becomes live acceptance."""
import numpy as np
from scipy.stats import chi2
from .reference import ReferencePipeline

def evaluate_predictions(truth,prediction,status):
    if not len(truth) or not len(truth)==len(prediction)==len(status):raise ValueError('Aligned nonempty records required')
    allowed={'OK','REST','REJECT','UNKNOWN','INVALID','WARMUP'}
    if any(s not in allowed for s in status):raise ValueError('Unknown status')
    known=[i for i,t in enumerate(truth) if t!='UNKNOWN'];accepted=[i for i in known if status[i] in ['OK','REST'] and prediction[i] is not None]
    correct=sum(truth[i]==prediction[i] for i in accepted)
    return {'total_labeled':len(known),'unknown_count':len(truth)-len(known),'accepted':len(accepted),
            'coverage':len(accepted)/len(known) if known else None,
            'conditional_accuracy':correct/len(accepted) if accepted else None,
            'success_including_rejection':correct/len(known) if known else None,
            'status_counts':{s:status.count(s) for s in sorted(allowed)}}

def poisson_upper(events,exposure_minutes,confidence=.95):
    if type(events) is not int or events<0 or not np.isfinite(exposure_minutes) or exposure_minutes<=0 or not 0<confidence<1:raise ValueError('Positive exposure and valid event count required')
    return float(.5*chi2.ppf(confidence,2*(events+1))/exposure_minutes)

def cluster_interval(session_outcomes,seed=17,repetitions=2000):
    if not session_outcomes or any(not len(v) for v in session_outcomes.values()):raise ValueError('Nonempty independent groups required')
    means=np.array([np.mean(v) for v in session_outcomes.values()],float)
    if not np.isfinite(means).all() or np.any((means<0)|(means>1)):raise ValueError('Finite event fractions required')
    rng=np.random.default_rng(seed);samples=means[rng.integers(0,len(means),(repetitions,len(means)))].mean(axis=1)
    return {'estimate':float(means.mean()),'interval_95':np.quantile(samples,[.025,.975]).tolist(),
            'n_groups':len(means),'high_confidence':False,
            'interpretation':'descriptive cluster bootstrap; evidence strength requires independent design and sufficient groups'}

def grouped_cv(trials,labels,groups,channels):
    x=np.asarray(trials);y=np.asarray(labels);g=np.asarray(groups)
    if len(x)!=len(y) or len(x)!=len(g) or len(set(g))<3:raise ValueError('At least three independent groups required')
    folds=[]
    for held in np.unique(g):
        train=g!=held;test=~train
        model=ReferencePipeline().fit(x[train],y[train],channels)
        pred=model.predict(x[test],channels)['labels']
        folds.append({'held_group':str(held),'train_groups':np.unique(g[train]).tolist(),
                      'train_indices':np.flatnonzero(train).tolist(),'test_indices':np.flatnonzero(test).tolist(),
                      'accuracy':float(np.mean(pred==y[test]))})
    return {'folds':folds,'selection':'fixed settings; hyperparameter search requires separate inner groups',
            'independent_acceptance':False}

def _balanced_accuracy(truth,prediction):
    classes=np.unique(truth)
    return float(np.mean([np.mean(prediction[truth==c]==c) for c in classes]))

def nested_group_cv(trials,labels,groups,channels,candidates):
    """Entire spatial/normalization/classifier fit repeated inside every split.

    Groups must already represent whole source/date/wearing units validated by
    contracts. No filter/window/threshold search outside the supplied candidates.
    This method does not make a reused development set an independent test.
    """
    x=np.asarray(trials);y=np.asarray(labels);g=np.asarray(groups)
    if len(x)!=len(y) or len(x)!=len(g) or len(set(g))<4 or not candidates:raise ValueError('At least four independent groups and fixed candidates required')
    outer=[]
    for held in np.unique(g):
        train=g!=held;test=~train;selection=[]
        for options in candidates:
            inner=[]
            for inner_held in np.unique(g[train]):
                itrain=train&(g!=inner_held);ival=train&(g==inner_held)
                m=ReferencePipeline(**options).fit(x[itrain],y[itrain],channels)
                pred=m.predict(x[ival],channels)['labels'];inner.append(_balanced_accuracy(y[ival],pred))
            selection.append(float(np.mean(inner)))
        chosen=int(np.argmax(selection));m=ReferencePipeline(**candidates[chosen]).fit(x[train],y[train],channels)
        pred=m.predict(x[test],channels)['labels']
        outer.append({'held_group':str(held),'selection_groups':np.unique(g[train]).tolist(),
                      'candidate_inner_BA':selection,'selected_candidate':candidates[chosen],
                      'outer_BA':_balanced_accuracy(y[test],pred),
                      'train_indices':np.flatnonzero(train).tolist(),'test_indices':np.flatnonzero(test).tolist()})
    return {'outer_folds':outer,'independent_acceptance':False,'evidence':'grouped algorithm comparison only; not live validation'}

def permute_label_blocks(labels,blocks,seed=17):
    """Whole label-block permutation under a PREDECLARED exchangeability null.

    Refuses unequal blocks instead of treating overlapping frames as independent.
    Caller must restrict it to legitimate exchangeability strata; no automatic
    p-value or significance claim is made from this helper alone.
    """
    y=np.asarray(labels);g=np.asarray(blocks)
    if y.ndim!=1 or g.shape!=y.shape or len(set(g))<2:raise ValueError('Aligned independent blocks required')
    where=[np.flatnonzero(g==k) for k in np.unique(g)]
    if len({len(i) for i in where})!=1:raise ValueError('Equal block sizes required; choose a valid null design')
    rng=np.random.default_rng(seed);order=rng.permutation(len(where));result=y.copy()
    for dst,src in enumerate(order):result[where[dst]]=y[where[src]]
    return result
