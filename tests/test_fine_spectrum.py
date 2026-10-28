import numpy as np
from gnautilus_ml.fine_spectrum import CausalSpectrum,select_view

def test_physical_channels_and_continuous_chunk_identity():
    t=np.arange(1000)/250;x=np.zeros((32,len(t)));x[15]=np.sin(2*np.pi*10*t);x[18]=np.sin(2*np.pi*20*t)
    a=CausalSpectrum();full,ready=a.step(x);b=CausalSpectrum()
    for z in np.array_split(x,40,axis=1):split,ok=b.step(z)
    np.testing.assert_allclose(split,full,atol=1e-7);assert ready and ok
    p=full[:1440].reshape(32,45);assert np.argmax(p[15])==4 and np.argmax(p[18])==24
    v=select_view(full,[15,18],'absolute');np.testing.assert_array_equal(v[:45],p[15]);np.testing.assert_array_equal(v[45:90],p[18])

def test_causal_prefix_gain_invariance_and_real_warmup():
    rng=np.random.default_rng(17);x=rng.normal(size=(32,700));a=CausalSpectrum();prefix,ready=a.step(x[:,:499]);assert not ready and a.samples==499
    prefix_copy=prefix.copy();a.step(x[:,499:]);np.testing.assert_array_equal(prefix,prefix_copy)
    b=CausalSpectrum();p,ready=b.step(x);c=CausalSpectrum();q,_=c.step(x*np.linspace(.5,2,32)[:,None]);assert ready
    np.testing.assert_allclose(select_view(p,list(range(8,32)),'shape'),select_view(q,list(range(8,32)),'shape'),atol=3e-6)


def test_fine_artifact_stream_chunking_and_no_fake_history(tmp_path):
    from tests.test_intent_repair import repaired_model
    from tests.test_eye_evidence import CONFIG
    from gnautilus_ml.fine_navigation import FineHybridModel,RUNTIME
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    old=repaired_model();a={k:v.copy() for k,v in old.arrays.items()};n=int(np.count_nonzero(a['navigation_input_weights']));dimension=45*n+n*(n-1)//2+32
    for prefix,classes in [('nav__active__',2),('nav__direction__',4)]:
        a[prefix+'mean']=np.zeros(dimension,dtype='float32');a[prefix+'scale']=np.ones(dimension,dtype='float32');a[prefix+'weight']=np.arange(classes*dimension,dtype='float32').reshape(classes,dimension)*.0001
    m=FineHybridModel(a,dict(old.details,spectral_view_mode='shape',eye_event_config=CONFIG),'cpu');x=np.random.default_rng(15).normal(size=(32,700)).astype('float32');s=m.new_session();assert s.step(x[:,:499])['warming']
    whole=m.new_session().step(x);s=m.new_session()
    for at in range(0,700,17):chunk=s.step(x[:,at:at+17])
    np.testing.assert_allclose(chunk['probabilities'],whole['probabilities'],atol=1e-6);assert chunk['navigation_decision']==whole['navigation_decision']
    path=save_artifact(tmp_path,m,dict(runtime_schema=RUNTIME,evaluation_stage='synthetic',runtime_accepted=False));loaded,meta=load_artifact(path,device='cpu');assert meta['window_s']==2.;np.testing.assert_allclose(loaded.new_session().step(x)['probabilities'],whole['probabilities'],atol=1e-6)
