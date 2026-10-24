import copy,json
from pathlib import Path
from unittest.mock import Mock,patch
import pytest
from gnautilus_ml.common import atomic

PROFILE=dict(host='172.25.56.83',port=5522,username='sysu',hostkey_sha256='a'*64,root='/isolated',service_port=18871,gpu_uuid='gpu-fixture')

def manager(tmp_path,cache=None):
 from hub.ml_runtime import MLManager
 if cache:atomic(tmp_path/'hub/runtime/gnautilus_ml_cache.json',cache)
 bridge=Mock();bridge.status={'ssh_connected':False,'worker_connected':False,'connecting':False};bridge.auto_connect_suspended=False;bridge.password='fixture-only';bridge.connect.return_value=False
 hub=Mock();m=MLManager(hub,tmp_path,bridge);return m,bridge,hub

def test_startup_uses_saved_credential_and_schedules_connection(tmp_path):
 m,b,h=manager(tmp_path);m.connection_tick(now=1)
 b.connect.assert_called_once_with()
 assert not h.intent.start.called

def test_ssh_alive_but_service_failed_reconnects_with_backoff(tmp_path):
 m,b,h=manager(tmp_path);b.status.update(ssh_connected=True,worker_connected=False);m.connection_tick(now=1);m.connection_tick(now=1.1)
 assert b.connect.call_count==1

def test_explicit_disconnect_is_not_undone_by_autoconnect(tmp_path):
 m,b,h=manager(tmp_path);b.auto_connect_suspended=True;m.connection_tick(now=1);b.connect.assert_not_called()

def test_restore_only_original_selected_model_and_never_start_inference(tmp_path):
 m,b,h=manager(tmp_path);mid='b'*64
 m.cached={'selected':mid,'loaded':None,'models':[{'model_id':mid,'weights_sha256':'c'*64,'runtime_accepted':False,'algorithm':'csp_lda'}]};m.updated=1;b.status.update(ssh_connected=True,worker_connected=True)
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
