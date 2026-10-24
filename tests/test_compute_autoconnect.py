import copy,json
from pathlib import Path
from unittest.mock import Mock,patch
import pytest
from gnautilus_ml.common import atomic

PROFILE=dict(host='172.25.56.83',port=5522,username='sysu',hostkey_sha256='a'*64,root='/isolated',service_port=18871,gpu_uuid='gpu-fixture')

def manager(tmp_path,cache=None):
 from hub.ml_runtime import MLManager
 if cache:atomic(tmp_path/'hub/runtime/gnautilus_ml_cache.json',cache)
 bridge=Mock();bridge.status={'ssh_connected':False,'worker_connected':False,'connecting':False};bridge.auto_connect_suspended=False;bridge.connection_epoch=0;bridge.password='fixture-only';bridge.connect.return_value=False
 hub=Mock();m=MLManager(hub,tmp_path,bridge);return m,bridge,hub

def test_startup_uses_saved_credential_and_schedules_connection(tmp_path):
 m,b,h=manager(tmp_path);m.connection_tick(now=1)
 b.connect.assert_called_once_with(expected_epoch=0)
 assert not h.intent.start.called

def test_ssh_alive_but_service_failed_reconnects_with_backoff(tmp_path):
 m,b,h=manager(tmp_path);b.status.update(ssh_connected=True,worker_connected=False);m.connection_tick(now=1);m.connection_tick(now=1.1)
 assert b.connect.call_count==1

def test_explicit_disconnect_is_not_undone_by_autoconnect(tmp_path):
 m,b,h=manager(tmp_path);b.auto_connect_suspended=True;m.connection_tick(now=1);b.connect.assert_not_called()

def test_restore_only_original_selected_model_and_never_start_inference(tmp_path):
 m,b,h=manager(tmp_path);mid='b'*64
 m.cached={'selected':mid,'loaded':None,'models':[{'model_id':mid,'weights_sha256':'c'*64,'runtime_accepted':False,'algorithm':'csp_lda'}]};m.updated=__import__('time').time();b.status.update(ssh_connected=True,worker_connected=True)
 def call(method,path,data=None,**kw):
  if path=='/snapshot':return {**m.cached,'loaded':mid}
  return {}
 b.call.side_effect=call;m.restore_selected(now=1)
 assert b.call.call_args_list[0].args[:2]==('POST','/models/activate')
 assert b.call.call_args_list[0].args[2]['id']==mid
 assert not any('/inference/start' in str(c) for c in b.call.call_args_list)
 assert m.restore_state['state']=='loaded_offline'

def test_no_selected_model_does_not_choose_highest_scoring_model(tmp_path):
 m,b,h=manager(tmp_path);m.cached={'models':[{'model_id':'b'*64,'runtime_accepted':True}],'selected':None,'loaded':None};m.restore_selected(now=1);b.call.assert_not_called()
 assert m.restore_state['state']=='selection_required'

def test_stale_cache_is_not_claimed_as_loaded_or_usable(tmp_path):
 mid='b'*64;m,b,h=manager(tmp_path,{'loaded':mid,'selected':mid,'models':[]});s=m.snapshot()
 assert s['current_model_id'] is None
 assert s['selected_model_id']==mid
 assert not s['remote_fresh']

def test_vault_identity_binds_username_host_port_and_pinned_key():
 from gnautilus_ml.credentials import credential_target
 base=credential_target(PROFILE)
 for k,v in [('host','different'),('port',22),('username','other'),('hostkey_sha256','d'*64)]:
  assert credential_target({**PROFILE,k:v})!=base

def test_secret_vault_round_trip_does_not_write_project_files(tmp_path):
 from gnautilus_ml import credentials
 backend=Mock();backend.CRED_TYPE_GENERIC=1;backend.CRED_PERSIST_LOCAL_MACHINE=2;backend.CredRead.return_value={'CredentialBlob':'fixture-only'.encode('utf-16-le')}
 with patch.object(credentials,'_backend',return_value=backend):
  credentials.save_credential(PROFILE,'fixture-only');assert credentials.load_credential(PROFILE)=='fixture-only'
 assert list(tmp_path.iterdir())==[]
 assert backend.CredWrite.call_args.args[0]['CredentialBlob']== 'fixture-only'

def test_auth_failure_suppresses_retry_and_forgets_invalid_saved_credential(tmp_path):
 from gnautilus_ml.ssh import ComputeBridge
 atomic(tmp_path/'configs/gnautilus_compute.json',PROFILE)
 with patch('gnautilus_ml.ssh.load_credential',return_value='fixture-only'),patch('gnautilus_ml.ssh.forget_credential') as forget:
  bridge=ComputeBridge(tmp_path)
  import paramiko
  with patch('gnautilus_ml.ssh.socket.socket'),patch('gnautilus_ml.ssh.physical_interface',return_value=(None,None)),patch('gnautilus_ml.ssh.paramiko.SSHClient') as ssh:
   ssh.return_value.connect.side_effect=paramiko.AuthenticationException('bad auth');bridge.connect()
  assert bridge.password is None and bridge.auto_connect_suspended
  forget.assert_called_once()

def test_readiness_distinguishes_offline_model_from_connection_failure(tmp_path):
 import time
 m,b,h=manager(tmp_path);mid='b'*64;m.cached={'selected':mid,'loaded':mid,'models':[{'model_id':mid,'algorithm':'csp_lda','runtime_accepted':False}]}
 assert m.inference_readiness()['code']=='compute_disconnected'
 b.status.update(ssh_connected=True,worker_connected=True);m.updated=time.time()
 result=m.inference_readiness();assert result['code']=='offline_model' and not result['can_start']
 m.cached['models'][0]['runtime_accepted']=True
 assert m.inference_readiness()['can_start']

def test_disconnect_during_authentication_cannot_publish_late_connection(tmp_path):
 from gnautilus_ml.ssh import ComputeBridge
 atomic(tmp_path/'configs/gnautilus_compute.json',PROFILE)
 with patch('gnautilus_ml.ssh.load_credential',return_value='fixture-only'),patch('gnautilus_ml.ssh.save_credential'),patch('gnautilus_ml.ssh.socket.socket'),patch('gnautilus_ml.ssh.physical_interface',return_value=(None,None)),patch('gnautilus_ml.ssh.paramiko.SSHClient') as ssh:
  b=ComputeBridge(tmp_path);ssh.return_value.connect.side_effect=lambda *a,**k:b.disconnect()
  ssh.return_value.open_sftp.return_value.__enter__.return_value.open.return_value.__enter__.return_value.read.return_value=b'fixture-token'
  b.call=Mock(return_value={'gpu_uuid':PROFILE['gpu_uuid'],'service':'private_gnautilus_ml'})
  b.connect()
  assert b.client is None and not b.status['worker_connected'] and not b.status['ssh_connected']
  assert b.auto_connect_suspended and b.status['connection_state']=='disconnected_by_operator'

def test_restore_does_not_use_stale_loaded_receipt(tmp_path):
 m,b,h=manager(tmp_path);mid='b'*64;m.cached={'selected':mid,'loaded':mid,'models':[{'model_id':mid,'algorithm':'csp_lda','runtime_accepted':True}]};m.error='timeout';m.updated=0
 m.restore_selected(now=1);assert m.restore_state['state']=='waiting_connection';b.call.assert_not_called()

def test_every_snapshot_revalidates_gpu_and_service_identity(tmp_path):
 from gnautilus_ml.ssh import ComputeBridge
 atomic(tmp_path/'configs/gnautilus_compute.json',PROFILE)
 with patch('gnautilus_ml.ssh.load_credential',return_value=None):b=ComputeBridge(tmp_path)
 b.client=Mock();b.token='fixture-token';b.client.get_transport().is_active.return_value=True;b.status.update(ssh_connected=True,worker_connected=True)
 payload=json.dumps({'gpu_uuid':'wrong','service':'unrelated-service'}).encode()
 with patch('gnautilus_ml.ssh.http.client.HTTPConnection') as connection:
  response=connection.return_value.getresponse.return_value;response.status=200;response.length=len(payload);response.read.return_value=payload
  with pytest.raises(ValueError,match='identity|UUID'):b.call('GET','/snapshot')
 assert not b.status['worker_connected'] and b.auto_connect_suspended

def test_token_subsystem_has_deadline_before_sftp_handshake_completes():
 import threading,time
 from gnautilus_ml.ssh import read_service_token
 closed=threading.Event();client=Mock();client.close.side_effect=closed.set
 def blocked():
  assert closed.wait(1),'deadline did not close the SSH transport'
  raise TimeoutError('fixture subsystem closed at deadline')
 client.open_sftp.side_effect=blocked;t=time.monotonic()
 with pytest.raises(TimeoutError):read_service_token(client,'/isolated/service.token',timeout=.04)
 assert time.monotonic()-t<.8
 client.close.assert_called_once()

def test_identity_failure_during_refresh_does_not_retry_in_same_tick(tmp_path):
 m,b,h=manager(tmp_path);b.status.update(ssh_connected=True,worker_connected=True)
 def fail(*a,**kw):
  b.auto_connect_suspended=True;b.status.update(ssh_connected=False,worker_connected=False);raise ValueError('service identity mismatch')
 b.call.side_effect=fail;m.connection_tick(now=1);b.connect.assert_not_called()

@pytest.mark.parametrize('connected,error',[(False,None),(True,'link failed')])
def test_recent_training_cache_is_not_fresh_without_verified_connection(tmp_path,connected,error):
 import time
 m,b,h=manager(tmp_path);m.updated=time.time();m.error=error;b.status.update(ssh_connected=connected,worker_connected=connected)
 assert not m.training()['fresh']
 assert m.snapshot()['restore']['state']=='waiting_connection'

def test_suspended_connection_does_not_promise_automatic_retry(tmp_path):
 m,b,h=manager(tmp_path);b.auto_connect_suspended=True
 assert '自动重连' not in m.inference_readiness()['message']

def test_retry_dispatched_before_operator_disconnect_cannot_reopen_link(tmp_path):
 from gnautilus_ml.ssh import ComputeBridge
 from hub.ml_runtime import MLManager
 atomic(tmp_path/'configs/gnautilus_compute.json',PROFILE)
 with patch('gnautilus_ml.ssh.load_credential',return_value='fixture-only'):
  b=ComputeBridge(tmp_path)
 m=MLManager(Mock(),tmp_path,b);connect=b.connect
 def interrupted(*a,**kw):
  b.disconnect();return connect(*a,**kw)
 with patch.object(b,'connect',side_effect=interrupted),patch('gnautilus_ml.ssh.physical_interface',side_effect=AssertionError('Must not open a socket after manual disconnect')) as interface:
  m.connection_tick()
 assert b.auto_connect_suspended and b.status['connection_state']=='disconnected_by_operator'
 interface.assert_not_called()
