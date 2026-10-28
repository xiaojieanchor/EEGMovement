import numpy as np

def test_band_ratio_removes_per_electrode_gain_without_mixing_channel_identity():
    from gnautilus_ml.spectral_views import full_spectrum,select_view
    rng=np.random.default_rng(4);history=rng.normal(size=(2,32,1500));gain=np.linspace(.25,4,32)[None,:,None];indices=[15,18,19,20]
    a=select_view(full_spectrum(history),indices,'band_ratio');b=select_view(full_spectrum(history*gain),indices,'band_ratio');np.testing.assert_allclose(a,b,atol=1e-5)
    assert not np.allclose(select_view(full_spectrum(history),indices,'absolute'),select_view(full_spectrum(history*gain),indices,'absolute'))

def test_physical_16_and_19_are_selected_in_place_and_not_swapped():
    from gnautilus_ml.spectral_views import full_spectrum,select_view
    history=np.ones((2,32,1500));history[:,15]*=3;history[:,18]*=5;history[1]*=2
    selected=select_view(full_spectrum(history),[15,18],'absolute');np.testing.assert_allclose(selected[:4],np.log([9,25,36,100]),atol=1e-6)

def test_temporal_contrast_removes_constant_power_offsets_and_rejects_unknown_view():
    import pytest
    from gnautilus_ml.spectral_views import full_spectrum,select_view
    x=np.random.default_rng(8).normal(size=(2,32,1500));a=full_spectrum(x);b=a.copy();b[:256]+=2
    np.testing.assert_allclose(select_view(a,range(8),'temporal_contrast'),select_view(b,range(8),'temporal_contrast'),atol=1e-5)
    with pytest.raises(ValueError):select_view(a,range(8),'unknown')

def test_kernel_map_has_literal_distances_and_fitting_requires_authority():
    import pytest
    from gnautilus_ml.kernel_readout import kernel_features,fit_kernel
    arrays=dict(kernel_mean=np.array([0.,0.]),kernel_scale=np.array([1.,1.]),kernel_centers=np.array([[0.,0.],[1.,1.]]),kernel_gamma=np.array(1.))
    np.testing.assert_allclose(kernel_features(np.array([0.,0.]),arrays),[1.,.36787944],atol=1e-7)
    with pytest.raises(RuntimeError,match='governed'):fit_kernel(None,None,None,1.)

def test_spectral_kernel_artifact_roundtrip_and_causal_packet_equivalence(tmp_path):
    from tests.test_intent_repair import repaired_model
    from tests.test_eye_evidence import CONFIG
    from gnautilus_ml.spectral_navigation import SpectralHybridModel,RUNTIME
    from gnautilus_ml.artifacts import save_artifact,load_artifact
    old=repaired_model();a={k:v.copy() for k,v in old.arrays.items()};a.update(nav__kernel_mean=np.zeros(54,dtype='float32'),nav__kernel_scale=np.ones(54,dtype='float32'),nav__kernel_centers=np.stack([np.zeros(54),np.ones(54)]).astype('float32'),nav__kernel_gamma=np.array(1,dtype='float32'))
    for prefix,classes in [('nav__active__',2),('nav__direction__',4)]:
        a[prefix+'mean']=np.zeros(2,dtype='float32');a[prefix+'scale']=np.ones(2,dtype='float32');a[prefix+'weight']=np.arange(classes*2,dtype='float32').reshape(classes,2)*.01
    model=SpectralHybridModel(a,dict(old.details,spectral_view_mode='band_ratio',eye_event_config=CONFIG),'cpu');x=np.random.default_rng(3).normal(size=(32,700)).astype('float32');whole=model.new_session().step(x);s=model.new_session()
    for at in range(0,700,17):chunk=s.step(x[:,at:at+17])
    np.testing.assert_allclose(whole['probabilities'],chunk['probabilities'],atol=1e-6);assert whole['navigation_decision']==chunk['navigation_decision'];path=save_artifact(tmp_path,model,dict(runtime_schema=RUNTIME,evaluation_stage='synthetic',runtime_accepted=False));loaded,_=load_artifact(path,device='cpu');np.testing.assert_allclose(loaded.new_session().step(x)['probabilities'],whole['probabilities'],atol=1e-6)
