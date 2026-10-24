"""Trusted compact causal architecture; arrays contain no executable pickle."""
import numpy as np

def build(channels,classes):
    import torch
    from torch import nn
    class Net(nn.Module):
        def __init__(self):
            super().__init__();self.temporal=nn.Conv2d(1,8,(1,17));self.spatial=nn.Conv2d(8,16,(channels,1),groups=8)
            self.gru=nn.GRU(16,32,batch_first=True);self.head=nn.Linear(32,classes)
        def forward(self,x):
            # Only left padding. Pooling contains only samples already in the history.
            z=torch.nn.functional.elu(self.spatial(self.temporal(torch.nn.functional.pad(x[:,None],(16,0)))))
            z=torch.nn.functional.avg_pool2d(z,(1,4)).squeeze(2).transpose(1,2)
            z,_=self.gru(z);return self.head(z[:,-1])
    return Net()

def fit_network(x,y,classes,seed,epochs,device,progress,checkpoint_path=None,validation=None,training_groups=None):
    import torch,time,json,os
    from pathlib import Path
    from .common import atomic,file_hash,read_json
    x=np.asarray(x,dtype=np.float32)
    torch.manual_seed(seed);net=build(x.shape[1],len(classes)).to(device);mean=x.mean((0,2),keepdims=True);scale=np.maximum(x.std((0,2),keepdims=True),1e-5)
    xx=torch.as_tensor((x-mean)/scale,device=device);yy=torch.as_tensor(np.searchsorted(classes,y),device=device,dtype=torch.long)
    optimizer=torch.optim.AdamW(net.parameters(),lr=.001,weight_decay=.01);rng=np.random.default_rng(seed);start=time.monotonic();first=0
    best=None;best_score=-1.;best_epoch=None;history=[]
    vx=vy=vg=None
    if validation is not None:
        vx,vy,vg=validation
        if not np.array_equal(np.unique(vy),classes):raise ValueError('Development classes differ from training classes')
        vx=torch.as_tensor((np.asarray(vx,dtype=np.float32)-mean)/scale,device=device)
        vy=np.asarray(vy);vg=np.asarray(vg)
    def evaluate(inputs,targets,groups=None):
        net.eval();pred=[];loss_sum=0.
        with torch.inference_mode():
            for begin in range(0,len(targets),128):
                z=net(inputs[begin:begin+128]);truth=torch.as_tensor(np.searchsorted(classes,targets[begin:begin+128]),device=device,dtype=torch.long)
                loss_sum+=float(torch.nn.functional.cross_entropy(z,truth,reduction='sum'))
                pred.extend(classes[z.argmax(-1).cpu().numpy()])
        pred=np.asarray(pred)
        def ba(at):return float(np.mean([np.mean(pred[at][targets[at]==c]==c) for c in np.unique(targets[at])]))
        return dict(loss=loss_sum/len(targets),accuracy=float(np.mean(pred==targets)),balanced_accuracy=float(np.mean([ba(groups==g) for g in np.unique(groups)])) if groups is not None else ba(np.ones(len(targets),bool)))
    cp=Path(checkpoint_path) if checkpoint_path else None
    if cp and cp.with_suffix('.json').exists():
        receipt=read_json(cp.with_suffix('.json'))
        committed=cp.parent/receipt.get('file',cp.name)
        if committed.parent.resolve()!=cp.parent.resolve() or file_hash(committed)!=receipt['sha256']:raise ValueError('Resume checkpoint commit/hash mismatch')
        state=torch.load(committed,map_location=device,weights_only=True)
        if state['seed']!=seed or state['classes']!=classes.tolist() or state['shape']!=list(x.shape):raise ValueError('Resume identity differs from task')
        net.load_state_dict(state['model']);optimizer.load_state_dict(state['optimizer']);first=state['epoch'];rng.bit_generator.state=json.loads(state['numpy_rng']);torch.set_rng_state(state['torch_rng'].cpu())
        if str(device).startswith('cuda'):torch.cuda.set_rng_state_all([v.cpu() for v in state['cuda_rng']])
        best=state.get('best_model');best_score=state.get('best_score',-1.);best_epoch=state.get('best_epoch');history=state.get('history',[])
    for epoch in range(first,epochs):
        net.train();total=0.;gradient_norms=[];module_gradients={}
        for ids in np.array_split(rng.permutation(len(x)),max(1,int(np.ceil(len(x)/32)))):
            optimizer.zero_grad(set_to_none=True);loss=torch.nn.functional.cross_entropy(net(xx[ids]),yy[ids]);loss.backward()
            if not module_gradients:
                for name in ['temporal','spatial','gru','head']:
                    module_gradients[name]=float(torch.sqrt(sum(torch.sum(p.grad.detach()**2) for p in getattr(net,name).parameters() if p.grad is not None)))
                if min(module_gradients.values())<=0:raise ValueError('An actual neural branch has zero supervised gradient')
            norm=torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
            if not torch.isfinite(loss) or not torch.isfinite(norm) or float(norm)==0:raise ValueError('Nonfinite/zero gradient or loss in actual training')
            gradient_norms.append(float(norm));optimizer.step();total+=float(loss.detach())*len(ids)
        train_metrics=evaluate(xx,np.asarray(y),np.asarray(training_groups) if training_groups is not None else None);row=dict(epoch=epoch+1,total_epochs=epochs,loss=total/len(x),training_loss=train_metrics['loss'],training_accuracy=train_metrics['accuracy'],
            training_balanced_accuracy=train_metrics['balanced_accuracy'],gradient_norm_min=min(gradient_norms),gradient_norm_max=max(gradient_norms),first_batch_module_gradient_norms=module_gradients,precision='float32',elapsed_s=time.monotonic()-start)
        if validation is not None:
            dev_metrics=evaluate(vx,vy,vg);score=dev_metrics['balanced_accuracy']
            row.update(development_loss=dev_metrics['loss'],development_accuracy=dev_metrics['accuracy'],development_balanced_accuracy=score)
            if score>best_score:
                best={k:v.detach().cpu().clone() for k,v in net.state_dict().items()};best_score=score;best_epoch=epoch+1
            row.update(selected_epoch=best_epoch,selected_development_balanced_accuracy=best_score)
        history.append(row)
        if cp:
            cp.parent.mkdir(parents=True,exist_ok=True);temp=cp.with_suffix('.tmp')
            torch.save({'model':net.state_dict(),'optimizer':optimizer.state_dict(),'epoch':epoch+1,'seed':seed,'shape':list(x.shape),'classes':classes.tolist(),
                        'numpy_rng':json.dumps(rng.bit_generator.state),'torch_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state_all() if str(device).startswith('cuda') else [],
                        'best_model':best,'best_score':best_score,'best_epoch':best_epoch,'history':history},temp)
            with temp.open('r+b') as f:f.flush();os.fsync(f.fileno())
            # Commit a new immutable payload before replacing the small pointer.
            # A crash here leaves the previous pointer and payload recoverable.
            checksum=file_hash(temp);payload=cp.with_name(cp.stem+'.'+checksum+'.pt')
            os.replace(temp,payload);atomic(cp.with_suffix('.json'),{'file':payload.name,'sha256':checksum,'epoch':epoch+1})
            for old in cp.parent.glob(cp.stem+'.*.pt'):
                if old!=payload:old.unlink(missing_ok=True)
        if progress:progress(row)
    if best is not None:net.load_state_dict(best)
    arrays={'input_mean':mean,'input_scale':scale};arrays.update({'net__'+k:v.detach().cpu().numpy() for k,v in net.state_dict().items()})
    return arrays,{'epochs':epochs,'selected_epoch':best_epoch if best is not None else epochs,'selection':'development_group_macro_BA_tie_earliest' if best is not None else 'last_fixed_epoch',
                  'training_history':history,'score_semantics':'uncalibrated_neural_softmax','history':'finite_trailing_window','gru_hidden':32}

def predict_network(x,arrays,classes,device=None):
    import torch
    if device is None:device='cuda:0' if torch.cuda.is_available() else 'cpu'
    net=build(x.shape[1],classes).to(device);net.load_state_dict({k[5:]:torch.as_tensor(v.copy(),device=device) for k,v in arrays.items() if k.startswith('net__')});net.eval()
    with torch.inference_mode():p=net(torch.as_tensor((x-arrays['input_mean'])/arrays['input_scale'],device=device)).softmax(-1).cpu().numpy()
    return p

class Predictor:
    def __init__(self,arrays,channels,classes):
        import torch
        self.device='cuda:0' if torch.cuda.is_available() else 'cpu';self.net=build(channels,classes).to(self.device)
        self.net.load_state_dict({k[5:]:torch.as_tensor(v.copy(),device=self.device) for k,v in arrays.items() if k.startswith('net__')});self.net.eval()
        self.mean=torch.as_tensor(arrays['input_mean'],device=self.device);self.scale=torch.as_tensor(arrays['input_scale'],device=self.device)
    def __call__(self,x):
        import torch
        with torch.inference_mode():return np.concatenate([self.net((torch.as_tensor(x[at:at+128],device=self.device)-self.mean)/self.scale).softmax(-1).cpu().numpy() for at in range(0,len(x),128)])
