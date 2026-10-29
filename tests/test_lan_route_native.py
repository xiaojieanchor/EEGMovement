"""Execute the same C# LAN probe used by Quest against isolated HTTP/UDP services."""
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hub.quest import QuestService, execution_key, private_ipv4
from hub.vr import VrLink

ROOT=Path(__file__).resolve().parents[1]
COMPILER=Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/MSBuild/Current/Bin/Roslyn/csc.exe')


@pytest.fixture(scope='module')
def native_probe(tmp_path_factory):
    if not COMPILER.exists():pytest.skip('Windows C# compiler unavailable')
    target=tmp_path_factory.mktemp('lan-native')/'LanRouteProbe.exe'
    files=[str(p) for p in (ROOT/'unity/EEGMovement/Assets/Scripts/Core').glob('*.cs')]
    subprocess.run([str(COMPILER),'/nologo','/utf8output','/target:exe','/main:LanRouteProbe','/langversion:latest','/r:System.Web.Extensions.dll','/r:System.Net.Http.dll','/out:'+str(target),*files,str(ROOT/'receiver/Harness.cs'),str(ROOT/'receiver/LanRouteProbe.cs')],check=True,capture_output=True,text=True,encoding='utf-8',errors='replace')
    import psutil
    hosts=[a.address for entries in psutil.net_if_addrs().values() for a in entries if a.family==socket.AF_INET and private_ipv4(a.address) and not a.address.startswith('127.')]
    if not hosts:pytest.skip('Private interface required for automatic LAN test')
    return target,hosts[0]


@pytest.mark.parametrize('fault,expected',[(None,0),('http_identity',2),('udp_identity',2),('udp_nonce',2)])
def test_native_probe_requires_both_end_to_end_http_and_udp(native_probe,tmp_path,fault,expected):
    target,host=native_probe;identity='a'*32;secret=bytes.fromhex('ab'*32)
    vr=VrLink(tmp_path,lambda:dict(),lambda *a:None,port=0,bind_host='0.0.0.0')
    hub=SimpleNamespace(vr=vr);service=QuestService(hub,tmp_path,port=0)
    service.register(dict(device_id=identity,secret=secret.hex(),address=host))
    vr.start();service.start()
    try:
        assert service.server and not service.error
        if fault=='udp_identity':vr.register_lan(identity,execution_key(b'0'*32))
        if fault=='udp_nonce':
            from hub.vr import decode_packet,encode_packet
            original=vr.process_datagram
            def wrong_nonce(raw,address):
                reply=decode_packet(original(raw,address),execution_key(secret));reply['nonce']='e'*32
                return encode_packet(reply,execution_key(secret))
            vr.process_datagram=wrong_nonce
        key='ac'*32 if fault=='http_identity' else secret.hex()
        result=subprocess.run([str(target),host,str(service.server.server_port),identity,key],capture_output=True,text=True,timeout=8)
        assert result.returncode==expected,(result.stdout,result.stderr)
        assert not vr.clients,'Probe must never replace or register an execution peer'
    finally:service.close();vr.close()


def test_quest_network_and_relay_compile_with_shared_core(native_probe,tmp_path):
    # Check production transport integration syntax; the stubs are deliberately
    # not used as runtime/graphics/Android acceptance evidence.
    files=[str(p) for p in (ROOT/'unity/EEGMovement/Assets/Scripts/Core').glob('*.cs')]
    runtime=ROOT/'unity/EEGMovement/Assets/Scripts/Runtime'
    result=subprocess.run([str(COMPILER),'/nologo','/utf8output','/target:library','/langversion:latest','/r:System.Web.Extensions.dll','/r:System.Net.Http.dll','/out:'+str(tmp_path/'QuestTransport.dll'),*files,str(runtime/'QuestNetwork.cs'),str(runtime/'PublicRelay.cs'),str(ROOT/'tests/QuestNetworkCompileStubs.cs')],capture_output=True,text=True,encoding='utf-8',errors='replace')
    assert result.returncode==0,result.stdout+result.stderr
