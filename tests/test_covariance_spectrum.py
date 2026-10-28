import numpy as np
from gnautilus_ml.covariance_spectrum import CausalCovariance,select_view

def test_full_montage_past_only_covariance_and_packet_identity():
    rng=np.random.default_rng(17);x=rng.normal(size=(32,1200));x[18]=.8*x[15]+.2*x[18];a=CausalCovariance();p,ready=a.step(x);b=CausalCovariance()
    for v in np.array_split(x,48,axis=1):q,ok=b.step(v)
    np.testing.assert_allclose(p,q,atol=1e-6);assert ready and ok;assert p.shape==(4096,)
    cov=p.reshape(2,2,32,32);assert cov[0,0,15,18]>0
    # Principal source block uses physical CH16 Cz and CH19 C5, in that order.
    f=select_view(p,[15,18],'logcov',500);assert f.shape==(6,) and np.isfinite(f).all()

def test_log_correlation_features_invariant_to_positive_electrode_gain():
    rng=np.random.default_rng(29);x=rng.normal(size=(32,1000));x[18]=x[15]*.5+x[18]*.5;a=CausalCovariance();p,_=a.step(x);b=CausalCovariance();q,_=b.step(x*np.linspace(.5,3.,32)[:,None]);ix=[15,18,19,21]
    np.testing.assert_allclose(select_view(p,ix,'logcorr',500),select_view(q,ix,'logcorr',500),atol=2e-6)
    c=CausalCovariance();_,ok=c.step(x[:,:499]);assert not ok and c.samples==499


def test_covariance_runtime_roundtrip_and_history(tmp_path):
    from tests.test_intent_repair import repaired_model
    from tests.test_eye_evidence import CONFIG
    from gnautilus_ml.covariance_navigation import CovarianceHybridModel,RUNTIME
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    old=repaired_model();a={k:v.copy() for k,v in old.arrays.items()};n=int(np.count_nonzero(a['navigation_input_weights']));dimension=n*(n+1)+32
    for prefix,classes in [('nav__active__',2),('nav__direction__',4)]:
        a[prefix+'mean']=np.zeros(dimension,dtype='float32');a[prefix+'scale']=np.ones(dimension,dtype='float32');a[prefix+'weight']=np.arange(classes*dimension,dtype='float32').reshape(classes,dimension)*.0001
    m=CovarianceHybridModel(a,dict(old.details,spectral_view_mode='logcov_500',eye_event_config=CONFIG),'cpu');x=np.random.default_rng(41).normal(size=(32,700)).astype('float32');s=m.new_session();assert s.step(x[:,:499])['warming'];whole=m.new_session().step(x);s=m.new_session()
    for at in range(0,700,17):chunk=s.step(x[:,at:at+17])
    np.testing.assert_allclose(chunk['probabilities'],whole['probabilities'],atol=1e-6);assert chunk['navigation_decision']==whole['navigation_decision'];path=save_artifact(tmp_path,m,dict(runtime_schema=RUNTIME,evaluation_stage='synthetic',runtime_accepted=False));loaded,meta=load_artifact(path,device='cpu');assert meta['window_s']==2.;np.testing.assert_allclose(loaded.new_session().step(x)['probabilities'],whole['probabilities'],atol=1e-6)
