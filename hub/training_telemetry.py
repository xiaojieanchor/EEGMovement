"""Read-only derivation from trainer receipts; never interpolates optimizer updates."""
import json,math

def finite(value):return type(value) in [int,float] and math.isfinite(value)

def receipt_progress(state):
    batch=state.get('batch');total=state.get('batches')
    if type(batch) is not int or type(total) is not int or not 0<=batch<=total or total<=0:return None
    return dict(epoch=state.get('epoch'),max_epochs=state.get('max_epochs'),batch=batch,batches=total,percent=100*batch/total,
                examples=state.get('examples_current_epoch'),total_examples=state.get('total_examples'),latest_loss=state.get('latest_loss'),steps=state.get('steps'))


def clean(value,depth=0):
    if depth>6:return None
    if isinstance(value,dict):return {k:clean(v,depth+1) for k,v in value.items() if not isinstance(v,float) or math.isfinite(v)}
    if isinstance(value,list):return [clean(v,depth+1) for v in value[:160]]
    if isinstance(value,str):return value[:600]
    if value is None or isinstance(value,bool) or finite(value):return value
    return None

def log_events(text,limit=70):
    fields=['updated_at','state','phase','steps','epoch','max_epochs','fold','candidate','seed','latest_loss','context_std','examples_current_epoch','training_fit_score','best_training_fit_score','validation_score','best_validation_score','trainable_parameters','train','validation','error']
    out=[]
    for line in text.splitlines():
        try:
            row=json.loads(line)
            if not isinstance(row,dict):continue
            item=clean({k:row[k] for k in fields if k in row})
            if item:out.append(item)
        except (ValueError,TypeError):continue
    return out[-limit:]

def batch_totals(rows):
    batches=examples=0
    for row in rows:
        if row.get('split')!='train':continue
        for length,count in row.get('counts',{}).items():
            size=256 if int(length)<=280 else 128 if int(length)<=536 else 32
            full,tail=divmod(int(count),size);batches+=full+int(tail>=2);examples+=int(count)-int(tail==1)
    return dict(batches=batches,examples=examples)

def large_progress(state,history,rows,previous=None):
    history=[r for r in history if isinstance(r,dict) and r.get('phase') in ['warm','full']]
    stage='full' if any(r['phase']=='full' for r in history) or 'full' in state.get('phase','') or state.get('detail_stage')=='full' else 'warm'
    done=[r for r in history if r['phase']==stage];last=done[-1] if done else None
    totals=batch_totals(rows) if stage=='full' else dict(batches=None,examples=None)
    steps=state.get('steps');last_steps=last.get('steps',0) if last else (history[-1].get('steps',0) if history else 0)
    completed=last['epoch'] if last else 0
    at_end=bool(last and finite(steps) and steps<=last_steps)
    explicit_start=state.get('phase')=='training_'+stage and state.get('epoch',0)>completed
    epoch=completed if at_end and not explicit_start else completed+1
    batch=totals['batches'] if at_end and not explicit_start else max(0,int(steps-last_steps)) if finite(steps) else None
    if stage=='warm' and done:
        previous_steps=done[-2]['steps'] if len(done)>1 else 0;totals=dict(batches=last_steps-previous_steps,examples=last.get('train',{}).get('examples'))
        if at_end and not explicit_start:batch=totals['batches']
    examples=totals['examples'] if at_end and not explicit_start else state.get('examples_current_epoch',0)
    cumulative=sum(r.get('train',{}).get('examples',0) for r in done)+(0 if at_end and not explicit_start else examples or 0)
    result=dict(stage=stage,epoch=epoch,max_epochs=50 if stage=='full' else 20,batch=batch,batches=totals['batches'],examples=examples,total_examples=totals['examples'],steps=steps,cumulative_examples=cumulative,updated_at=state.get('updated_at'),latest_loss=state.get('latest_loss'),context_std=state.get('context_std'),completed_epochs=completed,
                percent=min(100.,100*batch/totals['batches']) if finite(batch) and totals['batches'] else None,examples_per_second=None,batches_per_second=None,eta_seconds=None,basis='Confirmed optimizer updates / exact non-singleton batches from immutable corpus counts',trainer_report_interval_seconds=30)
    if previous and previous.get('stage',stage)==stage:
        elapsed=state.get('updated_at',0)-previous.get('updated_at',0)
        ds=steps-previous.get('steps',steps) if finite(steps) else 0;dx=cumulative-previous.get('cumulative_examples',cumulative)
        if 0<elapsed<180 and ds>0 and dx>0:
            result.update(examples_per_second=dx/elapsed,batches_per_second=ds/elapsed)
            if totals['batches'] and finite(batch):result['eta_seconds']=max(0,(totals['batches']-batch)/(ds/elapsed))
        elif elapsed==0 and ds==0 and cumulative==previous.get('cumulative_examples'):
            for key in ['examples_per_second','batches_per_second','eta_seconds']:result[key]=previous.get(key)
    return clean(result)

def loss_history(history):
    out=[]
    for row in history[-100:]:
        item=dict(epoch=row.get('epoch'),phase=row.get('phase'),train_loss=row.get('train',{}).get('loss',row.get('loss',row.get('train_loss'))),validation_loss=row.get('validation',{}).get('loss'),validation_score=row.get('selection_score'))
        out.append(clean(item))
    return out
