"""Bounded, read-only preparation evidence. Never opens private hub/runtime files.

Evidence is advisory: neither this module nor a successful software check enables
LIVE control. Disk work is cached independently of the acquisition lock.
"""
import copy
import datetime as dt
import hashlib
import json
import itertools
import math
import os
import threading
import time
from pathlib import Path


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_SOURCE_FILES = 4096
MAX_ARTIFACT_FILES = 512
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
UNITY_VALIDATION_CASES = frozenset(['stationary','forward','backward','left','right','test_expiry',
    'stop','emergency_reset','bad_signature','replay_ordering','network_loss','frame_stall',
    'reconnect_stopped','collision','telemetry_expiry','guided_acquisition'])
TRAINING_EVIDENCE = frozenset([
    'training_gpu_check.json', 'training_runner_check.json', 'training_data_audit.json',
    'training_environment.json', 'training_environment_freeze.txt', 'training_server_verification.json',
    'training_concurrent_link_observation.json', 'training_preflight.json', 'training_unit_tests.txt'])
UNITY_RAW_EVIDENCE=frozenset(['unity_udp_validation.json','execution_e2e.json','execution_soak_validation.json',
    'unity_frame_stall_validation.json','unity_safety_validation.json','unity_acquisition_validation.json'])
RELEASE_EVIDENCE=frozenset(['execution_validation.json','unity_runtime_validation.json','unity_acquisition_validation.json',
    'receiver_udp_validation.json','execution_e2e.json','signal_noise_validation.json','execution_soak_validation.json','pilot_schedule.json'])


def stamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def unity_sources(root):
    """Build identity inputs; excludes the generated identity to avoid recursion."""
    root = Path(root)
    project = root / 'unity/EEGMovement'
    files = set()
    for subtree in ['Assets','Packages','ProjectSettings']:
        for directory,dirs,names in os.walk(project/subtree):
            dirs[:]=[name for name in dirs if not name.endswith(('~','~.meta'))
                     and not (Path(directory)/name).is_symlink() and not (Path(directory)/name).is_junction()]
            for name in names:
                path=Path(directory)/name
                relative=path.relative_to(project).as_posix()
                if name.endswith(('~','~.meta')) or relative in ['Assets/Resources/build_identity.json','Assets/Resources/build_identity.json.meta']:
                    continue
                files.add(path)
                if len(files)>MAX_SOURCE_FILES:return sorted(files)
    return sorted(files)


def training_sources(root):
    root=Path(root)
    paths=list(itertools.islice((p for p in (root/'training').rglob('*.py')
               if '__pycache__' not in p.parts),MAX_SOURCE_FILES+1))
    paths.extend(root/name for name in ['scripts/training_gpu_check.py','scripts/training_runner_check.py'] if (root/name).is_file())
    return sorted(paths)


def source_build_id(hashes):
    if not isinstance(hashes,dict) or not 0<len(hashes)<=MAX_SOURCE_FILES:return None
    if not all(isinstance(name,str) and isinstance(value,str) and len(value)==64
               and all(char in '0123456789abcdef' for char in value) for name,value in hashes.items()):return None
    canonical=''.join(name+':'+hashes[name]+'\n' for name in sorted(hashes,key=lambda name:name.encode('utf-16-be')))
    return 'eeg-'+hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]


class PreparationMonitor:
    def __init__(self, root, unity_root=Path('G:/Unity'), cache_seconds=5, max_age_seconds=86400):
        self.root = Path(root).resolve()
        self.unity_root = Path(unity_root).resolve()
        self.cache_seconds = max(0, min(cache_seconds, 60))
        self.max_age_seconds = max_age_seconds
        self.lock = threading.Lock()
        self.cached = None
        self.cached_at = -math.inf
        self._hash_cache={}
        self._hash_budget=MAX_ARTIFACT_BYTES+MAX_SOURCE_BYTES*2
        self._refresh_gate=threading.Lock()
        self.refresh_thread=None
        self.refresh_error=None

    def _request_refresh(self):
        if not self._refresh_gate.acquire(blocking=False):return
        def refresh():
            try:
                self.snapshot({})
                self.refresh_error=None
            except Exception as error:
                self.refresh_error=type(error).__name__
            finally:self._refresh_gate.release()
        self.refresh_thread=threading.Thread(target=refresh,name='Preparation evidence refresh',daemon=True)
        self.refresh_thread.start()

    def engine_verified_hint(self,state):
        """Never block high-rate telemetry on full disk hashes or the evidence lock.

        Cached disk facts are immutable after publication. Expired/absent facts
        yield unknown/false while one background refresh runs; no old green is
        extended past its TTL. Explicit /api/preparation still verifies on demand.
        """
        cached=self.cached
        if cached is None or time.monotonic()-self.cached_at>=max(.25,self.cache_seconds):
            self._request_refresh();return False
        artifacts=cached.get('artifacts') or {};identity=artifacts.get('unity') or {}
        if not identity.get('build_verified') or not (artifacts.get('execution_validation') or {}).get('verified'):return False
        for stage in cached.get('stages',[]):
            if stage['id'] in ['unity_build','execution_validation']:
                checked=stamp(stage.get('checked_at'))
                if stage.get('status')!='ok' or checked is None or time.time()-checked>self.max_age_seconds:return False
        for peer in (state.get('vr') or {}).get('clients',[]):
            age=peer.get('telemetry_age_ms');health=peer.get('telemetry') or {}
            if (peer.get('role')=='unity' and peer.get('online') is True and type(age) in [int,float]
                    and 0<=age<300 and health.get('build_id')==identity.get('build_id')
                    and type(health.get('rendered_frames')) is int and health['rendered_frames']>0
                    and health.get('paused') is not True):return True
        return False

    def _json(self, path):
        try:
            with Path(path).open('rb') as stream:
                data = stream.read(MAX_JSON_BYTES+1)
            if len(data) > MAX_JSON_BYTES:
                return None
            result = json.loads(data.decode('utf-8-sig'), parse_constant=lambda _: None)
            return result if isinstance(result, dict) else None
        except (OSError, ValueError, UnicodeError, RecursionError):
            return None

    def _hash(self, path, max_bytes=MAX_JSON_BYTES):
        try:
            path=Path(path);before=path.stat()
            key=(str(path.resolve()),before.st_size,before.st_mtime_ns)
            if before.st_size>max_bytes:return None
            if key in self._hash_cache:return self._hash_cache[key]
            if before.st_size>self._hash_budget:return None
            self._hash_budget-=before.st_size
            with Path(path).open('rb') as stream:
                digest = hashlib.sha256()
                remaining = max_bytes
                while True:
                    data = stream.read(min(1024*1024, remaining+1))
                    if not data:
                        after=path.stat()
                        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):return None
                        result=digest.hexdigest();self._hash_cache[key]=result
                        return result
                    remaining -= len(data)
                    if remaining < 0:
                        return None
                    digest.update(data)
        except OSError:
            return None

    def _within(self, base, name):
        if not isinstance(name, str) or len(name) > 300:
            return None
        try:
            path = (base/name).resolve()
            return path if path.is_relative_to(base) else None
        except (OSError, ValueError):
            return None

    def _evidence(self, path, now, time_keys=('checked_at','finished_at','updated_at')):
        record = self._json(path)
        if record is None:
            return {}, 'unknown', None, None
        supplied=next((key for key in time_keys if key in record),None)
        checked=stamp(record[supplied]) if supplied else None
        if supplied and checked is None:
            return record, 'unknown', None, None
        if checked is None:
            try: checked = path.stat().st_mtime
            except OSError: return record, 'unknown', None, None
        age = (now-checked)*1000
        status = 'ok' if -60000 <= age <= self.max_age_seconds*1000 else 'warn'
        return record, status, iso(checked), max(0, round(age))

    def _matching_sources(self, record, files):
        hashes = record.get('source_hashes')
        if not isinstance(hashes, dict) or not hashes or not files or len(files)>MAX_SOURCE_FILES:
            return False
        expected = {p.relative_to(self.root).as_posix(): p for p in files}
        if any(not (p.resolve().is_relative_to(self.root/'training') or
                    p.resolve() in [self.root/'scripts/training_gpu_check.py',self.root/'scripts/training_runner_check.py'] or
                    p.resolve().is_relative_to(self.root/'unity/EEGMovement')) for p in files):
            return False
        if set(hashes) != set(expected):
            return False
        try:
            if sum(path.stat().st_size for path in files)>MAX_SOURCE_BYTES:return False
        except OSError:return False
        return all(isinstance(hashes[name], str) and self._hash(path,64*1024*1024)==hashes[name]
                   for name,path in expected.items())

    def _matching_artifacts(self, output, hashes):
        if output is None or not isinstance(hashes,dict) or not 0<len(hashes)<=MAX_ARTIFACT_FILES:
            return False
        base=output.parent
        files={}
        try:
            total=0
            for directory,dirs,names in os.walk(base):
                dirs[:]=[name for name in dirs if not (Path(directory)/name).is_symlink()
                         and not (Path(directory)/name).is_junction()]
                for name in names:
                    path=Path(directory)/name
                    if path.name=='build-status.json' or path.suffix.lower()=='.log':continue
                    if not path.resolve().is_relative_to(base.resolve()):return False
                    files[path.relative_to(base).as_posix()]=path
                    total+=path.stat().st_size
                    if len(files)>MAX_ARTIFACT_FILES or total>MAX_ARTIFACT_BYTES:return False
        except OSError:return False
        if set(files)!=set(hashes) or output.name not in files:return False
        return all(isinstance(expected,str) and self._hash(files[name],MAX_ARTIFACT_BYTES)==expected
                   for name,expected in hashes.items())

    def _matching_evidence(self, hashes, required):
        return (isinstance(hashes,dict) and set(hashes)==required and
                all(isinstance(expected,str) and self._hash(self.root/'outputs'/name)==expected
                    for name,expected in hashes.items()))

    def _disk(self, now):
        self._hash_cache={};self._hash_budget=MAX_ARTIFACT_BYTES+MAX_SOURCE_BYTES*2
        stages = []
        artifacts = {}
        def add(id, label, status, summary, path, checked=None, age=None):
            stages.append(dict(id=id,label=label,status=status,summary=summary,
                               source=str(path),checked_at=checked,age_ms=age))
        verify, validity, checked, age = self._evidence(self.unity_root/'editor-verify.json', now)
        verification_data=verify.get('data') if isinstance(verify.get('data'),dict) else {}
        version = verification_data.get('version')
        if not isinstance(version,str) or not version.replace('.','').isalnum() or len(version)>40:
            version='unverified'
        editor = self.unity_root/'Editors'/str(version)/'Editor/Unity.exe'
        installed = (validity=='ok' and verify.get('success') is True
                     and verification_data.get('ok') is True and editor.is_file())
        add('unity_install','Unity安装','ok' if installed else validity if validity!='ok' else 'blocked',
            '已核对Editor文件与安装记录' if installed else '安装记录缺失、过期或Editor文件不可用',
            self.unity_root/'editor-verify.json',checked,age)
        install, iv, ic, ia = self._evidence(self.unity_root/'installation-status.json',now)
        tools, tv, tc, ta = self._evidence(self.unity_root/'toolchain-verification.json',now)
        tool_checks = tools.get('checks',[])
        tools_ok = (tv=='ok' and tools.get('ready') is True and isinstance(tool_checks,list) and bool(tool_checks)
                    and len(tool_checks)<=40 and all(isinstance(c,dict) and c.get('present') is True
                    and (self._within(self.unity_root,c.get('path')) or Path('/__invalid__')).is_file() for c in tool_checks))
        working = iv=='ok' and install.get('phase')=='installing-android'
        add('android_toolchain','Android工具链','ok' if tools_ok else 'working' if working else tv if tv!='ok' else 'blocked',
            '工具链文件与验证记录可用' if tools_ok else 'Android模块正在安装' if working else '工具链尚无完整有效验证',
            self.unity_root/'toolchain-verification.json',tc,ta)
        build, bv, bc, ba = self._evidence(self.unity_root/'build-status.json',now)
        identity = build.get('build_id')
        identity_ok = isinstance(identity,str) and 0<len(identity)<=120
        source_ok = self._matching_sources(build,unity_sources(self.root)) and identity==source_build_id(build.get('source_hashes'))
        output = self._within(self.unity_root.parent/'EEGMovement',build.get('output'))
        output_ok = bool(output and isinstance(build.get('output_sha256'),str)
                         and self._hash(output,512*1024*1024)==build['output_sha256'])
        artifacts_ok=bool(bv=='ok' and identity_ok and source_ok and output_ok
                          and self._matching_artifacts(output,build.get('artifact_hashes')))
        build_ok = bv=='ok' and build.get('success') is True and identity_ok and source_ok and output_ok and artifacts_ok
        build_status = 'ok' if build_ok else 'unknown' if not identity_ok else 'blocked' if bv=='ok' else bv
        add('unity_build','Unity构建',build_status,
            'Windows构建、源码和产物摘要一致' if build_ok else '构建记录缺失身份、已过期或源码/产物摘要不一致',
            self.unity_root/'build-status.json',bc,ba)
        artifacts['unity'] = dict(installed=installed,build_verified=build_ok,
                                  build_id=identity if identity_ok else None,source_matches=source_ok,
                                  output_hash_matches=output_ok,artifact_hashes_match=artifacts_ok,android_toolchain_ready=tools_ok)
        android,av,ac,aa=self._evidence(self.unity_root/'android-build-status.json',now)
        apk=self._within(self.unity_root.parent/'EEGMovement',android.get('output'))
        android_sources=self._matching_sources(android,unity_sources(self.root))
        android_ok=(av=='ok' and android.get('success') is True and android.get('source_snapshot_unchanged') is True
                    and android_sources and android.get('build_id')==source_build_id(android.get('source_hashes'))
                    and apk is not None and apk.suffix.lower()=='.apk'
                    and self._hash(apk,MAX_ARTIFACT_BYTES)==android.get('output_sha256'))
        artifacts['quest_app']=dict(build_verified=bool(android_ok),build_id=android.get('build_id'),source_matches=android_sources)
        add('quest_build','Quest程序构建','ok' if android_ok else 'blocked',
            'APK与当前源码和构建摘要一致' if android_ok else '等待当前源码的Android构建摘要',self.unity_root/'android-build-status.json',ac,aa)
        runtime_validation, uv, uc, ua = self._evidence(self.root/'outputs/unity_runtime_validation.json',now)
        validation_sources_ok=self._matching_sources(runtime_validation,unity_sources(self.root))
        validation_cases=runtime_validation.get('cases')
        cases_ok=(isinstance(validation_cases,list) and 0<len(validation_cases)<=100
                  and all(isinstance(case,dict) and isinstance(case.get('id'),str)
                          and case.get('passed') is True for case in validation_cases))
        if cases_ok:
            ids=[case['id'] for case in validation_cases]
            cases_ok=len(ids)==len(set(ids)) and UNITY_VALIDATION_CASES<=set(ids)
        raw_evidence_ok=self._matching_evidence(runtime_validation.get('evidence_hashes'),UNITY_RAW_EVIDENCE)
        unity_acceptance_ok=(uv=='ok' and build_ok and validation_sources_ok and cases_ok and raw_evidence_ok
                             and runtime_validation.get('status')=='pass' and runtime_validation.get('build_id')==identity)
        runtime_evidence_hash=self._hash(self.root/'outputs/unity_runtime_validation.json')
        artifacts['unity_acceptance']=dict(verified=unity_acceptance_ok,source_matches=validation_sources_ok,
            checked_at=uc,age_ms=ua,evidence_sha256=runtime_evidence_hash,required_cases=len(UNITY_VALIDATION_CASES),evidence_hashes_match=raw_evidence_ok)
        release, ev, ec, ea = self._evidence(self.root/'outputs/execution_release_manifest.json',now)
        release_hashes=release.get('files')
        release_sources_ok=isinstance(release_hashes,dict) and 0<len(release_hashes)<=MAX_SOURCE_FILES
        if release_sources_ok:
            normalized={name.replace('\\','/'):value for name,value in release_hashes.items() if isinstance(name,str)}
            required={'hub/app.py','hub/vr.py','hub/preparation.py'} | {p.relative_to(self.root).as_posix() for p in unity_sources(self.root)}
            release_sources_ok=(len(normalized)==len(release_hashes) and required<=set(normalized))
            for name,expected in normalized.items():
                path=self._within(self.root,name)
                allowed=(path is not None and path.is_relative_to(self.root) and
                         path.parts[len(self.root.parts)] in ['hub','unity','receiver','scripts','configs','protocols']
                         and not path.is_relative_to(self.root/'hub/runtime'))
                if not allowed or not isinstance(expected,str) or self._hash(path,64*1024*1024)!=expected:
                    release_sources_ok=False;break
        release_unity=release.get('unity_build') if isinstance(release.get('unity_build'),dict) else {}
        release_evidence=release.get('evidence_files') if isinstance(release.get('evidence_files'),dict) else {}
        release_evidence_ok=self._matching_evidence(release_evidence,RELEASE_EVIDENCE)
        release_ok=(ev=='ok' and release_sources_ok and unity_acceptance_ok and release_evidence_ok and release.get('unity_runtime_verified') is True
                    and release.get('running_hub_source_matches') is True
                    and release.get('validated_core_and_binary_match') is True
                    and release_unity.get('build_id')==identity
                    and release_evidence.get('unity_runtime_validation.json')==runtime_evidence_hash)
        add('execution_validation','最终软件验收','ok' if release_ok else 'blocked' if ev=='ok' else ev,
            '当前源码、Unity构建身份与最终验收记录一致' if release_ok else '缺少当前Unity构建的完整最终验收记录',
            self.root/'outputs/execution_release_manifest.json',ec,ea)
        artifacts['execution_validation']=dict(verified=release_ok,source_matches=release_sources_ok)
        add('quest','Quest硬件验收','blocked','Quest头显显示、空间交互与佩戴体验尚未完成硬件验收',
            'hardware_acceptance_not_recorded')
        artifacts['quest_verified']=False
        report, rv, rc, ra = self._evidence(self.root/'outputs/training_readiness.json',now)
        sources=training_sources(self.root)
        hashes=report.get('evidence_hashes')
        evidence_ok = isinstance(hashes,dict) and set(hashes)==TRAINING_EVIDENCE
        if evidence_ok:
            evidence_ok=all(isinstance(h,str) and self._hash(self.root/'outputs'/name)==h for name,h in hashes.items())
        evidence_details=[]
        for name in sorted(TRAINING_EVIDENCE):
            path=self.root/'outputs'/name
            if path.suffix=='.json':
                document,validity,checked,age=self._evidence(path,now)
            else:
                document={}
                try:
                    modified=path.stat().st_mtime;checked=iso(modified);age=max(0,round((now-modified)*1000))
                    validity='ok' if -60<=now-modified<=self.max_age_seconds else 'warn'
                except OSError:validity='unknown';checked=None;age=None
            if name in ['training_gpu_check.json','training_runner_check.json'] and document.get('status')!='pass':validity='blocked'
            if name=='training_data_audit.json' and document.get('errors')!=[]:validity='blocked'
            evidence_details.append(dict(id=name,status=validity,checked_at=checked,age_ms=age,source='outputs/'+name))
        evidence_fresh=all(item['status']=='ok' for item in evidence_details)
        train_source_ok=self._matching_sources(report,sources)
        train_ok=(rv=='ok' and report.get('software_preparation_status')=='pass'
                  and report.get('source_mismatches')==[] and train_source_ok and evidence_ok and evidence_fresh)
        add('training_software','训练软件','ok' if train_ok else 'blocked' if rv=='ok' else rv,
            '软件与数值fixture证据有效；不代表意图准确率' if train_ok else '准备记录缺失、过期或源码/证据摘要不一致',
            self.root/'outputs/training_readiness.json',rc,ra)
        plan=self._json(self.root/'configs/training_plan.json') or {}
        for name in ['device_profile','manifest']:
            if not isinstance(plan.get(name),str):plan[name]=''
        profile_path=self._within(self.root/'configs',str(plan.get('device_profile','')).removeprefix('configs/'))
        profile=self._json(profile_path) if profile_path else None
        profile=profile or {}
        profile_evidence=profile.get('evidence_files')
        evidence_list_valid=(isinstance(profile_evidence,list) and 0<len(profile_evidence)<=29
                             and all(isinstance(p,str) for p in profile_evidence))
        montage_ok=(profile.get('verified') is True and profile.get('worn_quality_reviewed') is True
                    and bool(profile.get('reference')) and bool(profile.get('ground'))
                    and isinstance(profile.get('channel_order'),list) and len(profile['channel_order'])==7
                    and evidence_list_valid)
        gate=self._json(self.root/'outputs/training_preflight.json') or {}
        gate_hashes=report.get('formal_gate_hashes')
        required_gate_paths={'configs/training_plan.json',plan.get('device_profile'),plan.get('manifest')}
        if evidence_list_valid:
            required_gate_paths.update(profile_evidence)
        formal_hash_ok=(isinstance(gate_hashes,dict) and len(gate_hashes)<=32
                        and None not in required_gate_paths and required_gate_paths<=set(gate_hashes))
        if formal_hash_ok:
            formal_hash_ok=all(isinstance(expected,str) and (p:=self._within(self.root,name)) is not None and
                p.parts[len(self.root.parts)] in ['configs','data','outputs'] and self._hash(p)==expected
                for name,expected in gate_hashes.items())
        manifest=self._within(self.root/'data/personal',str(plan.get('manifest','')).removeprefix('data/personal/'))
        personal_exists=bool(manifest and manifest.is_file())
        formal_ok=(train_ok and montage_ok and personal_exists and formal_hash_ok
                   and report.get('formal_training_ready') is True and gate.get('formal_training_ready') is True
                   and gate.get('blockers')==[])
        add('training_data','个人标注数据','ok' if formal_ok else 'blocked',
            '个人数据与正式预检摘要一致' if formal_ok else '个人标注数据或正式预检证据尚未满足',
            'configs/training_plan.json + outputs/training_preflight.json',rc,ra)
        add('montage','设备线路与佩戴','ok' if montage_ok and formal_hash_ok else 'blocked',
            '线路、参考、地与佩戴证据已绑定预检' if montage_ok and formal_hash_ok else '待核对通道线路、参考、地和真实佩戴质量',
            'configs/training_device_profile.json',rc,ra)
        training=dict(software_ready=train_ok,formal_training_ready=formal_ok,source_matches=train_source_ok,
                      evidence_hashes_match=evidence_ok,personal_manifest_present=personal_exists,
                      device_profile_verified=montage_ok,formal_evidence_matches=formal_hash_ok,evidence=evidence_details)
        breakdown=report.get('breakdown')
        if isinstance(breakdown,dict):
            training['tests']={k:v for k,v in breakdown.items() if k in ['local_unit','gpu_numerical','actual_runner_fixture'] and type(v) is int and 0<=v<=100000}
        return dict(stages=stages,artifacts=artifacts,training=training)

    def snapshot(self, state):
        now=time.time()
        with self.lock:
            if self.cached is None or time.monotonic()-self.cached_at>=self.cache_seconds:
                self.cached=self._disk(now)
                self.cached_at=time.monotonic()
            result=copy.deepcopy(self.cached)
        # Refresh ages even while disk facts remain inside their bounded cache TTL.
        for stage in result['stages']:
            checked=stamp(stage['checked_at'])
            if checked is not None:
                stage['age_ms']=max(0,round((now-checked)*1000))
                if stage['status']=='ok' and stage['age_ms']>self.max_age_seconds*1000:
                    stage['status']='warn'
        stage_by_id={stage['id']:stage for stage in result['stages']}
        result['artifacts']['unity']['build_verified'] &= stage_by_id['unity_build']['status']=='ok'
        result['artifacts']['unity']['installed'] &= stage_by_id['unity_install']['status']=='ok'
        result['artifacts']['quest_app']['build_verified'] &= stage_by_id['quest_build']['status']=='ok'
        result['training']['software_ready'] &= stage_by_id['training_software']['status']=='ok'
        result['artifacts']['execution_validation']['verified'] &= stage_by_id['execution_validation']['status']=='ok'
        stages=result['stages']
        def add(id,label,ok,summary,age=None,status='offline'):
            stages.append(dict(id=id,label=label,status='ok' if ok else status,summary=summary,
                source='hub_snapshot',checked_at=iso(now-age/1000) if isinstance(age,(int,float)) else iso(now),age_ms=age))
        source=state.get('source') or {};server=state.get('server') or {};health=server.get('last_health') or {}
        live_source=source.get('mode')=='live' and source.get('fresh') is True
        add('acquisition','实时采集',live_source,'官方Socket实时数据新鲜' if live_source else
            '当前为公开或会话回放，不能视为实时设备采集' if source.get('mode') in ['replay','recording'] else '等待新鲜设备数据',
            source.get('age_ms'),status='warn' if source.get('mode') in ['replay','recording'] else 'offline')
        add('processing','本机处理',source.get('fresh') is True and (state.get('processing') or {}).get('filter_warm_samples',0)>=500,
            '因果滤波与连续窗口状态',source.get('age_ms'))
        add('ssh','SSH连接',server.get('ssh_connected') is True,'服务器连接状态',server.get('health_age_ms'))
        add('gpu','GPU处理',server.get('worker_connected') is True and health.get('ok') is True and state.get('result_fresh') is True,
            '新鲜GPU处理返回；不代表意图模型已校准',state.get('result_age_ms'))
        add('model','意图模型',False,'LIVE锁定：未通过个人意图校准与独立验证',server.get('health_age_ms'),status='blocked')
        clients=(state.get('vr') or {}).get('clients') or []
        online=[c for c in clients if c.get('online') is True and isinstance(c.get('telemetry_age_ms'),(int,float)) and 0<=c['telemetry_age_ms']<300]
        peer=next((c for c in online if c.get('role')=='unity'),next((c for c in online if c.get('role')=='csharp_harness'),None))
        unity=next((c for c in online if c.get('role')=='unity'),None)
        runtime_health=(unity or {}).get('telemetry') or {}
        identity=result['artifacts']['unity']
        android=result['artifacts']['quest_app']
        matches_windows=runtime_health.get('build_id')==identity['build_id'] and identity['build_verified']
        matches_quest=runtime_health.get('build_id')==android['build_id'] and android['build_verified'] and runtime_health.get('xr_active') is True
        rendered=bool(unity and type(runtime_health.get('rendered_frames')) is int and runtime_health['rendered_frames']>0
                      and runtime_health.get('paused') is not True
                      and (matches_windows or matches_quest))
        add('unity_runtime','Unity运行',rendered,'新鲜Unity主线程运行健康与当前构建一致' if rendered else
            'Unity离线' if not unity else 'Unity在线；当前构建身份或渲染健康待核验',
            (unity or {}).get('telemetry_age_ms'),status='warn' if unity else 'offline')
        quest=state.get('quest') or {}
        add('quest_lan','Quest局域网回执',rendered and matches_quest,'头显原生程序的签名LAN回执与当前APK一致',
            (unity or {}).get('telemetry_age_ms'))
        add('quest_preview','Quest画面回传',rendered and matches_quest and quest.get('mirror_fresh') is True,
            '应用单眼预览；2秒无新帧即过期，不代表物理显示延迟',quest.get('frame_age_ms'))
        add('execution','最终执行回执',bool(peer and (peer.get('telemetry') or {}).get('actual_speed') is not None
            and (peer.get('telemetry') or {}).get('actual_yaw_rate') is not None),
            'Unity真实应用回执' if peer and peer.get('role')=='unity' else 'C#联调真实应用回执' if peer else '无新鲜接收端回执',
            (peer or {}).get('telemetry_age_ms'))
        actions={'unity_install':'核验G盘Editor安装与安装记录','unity_build':'从当前源码构建并记录build_id、source_hashes、产物摘要',
                 'android_toolchain':'完成Android模块安装并重新验证工具链','training_software':'重新运行训练软件readiness检查',
                 'training_data':'采集带标签个人数据并重新执行正式预检','montage':'核对设备通道、参考和地并记录佩戴质量',
                 'acquisition':'检查设备Socket与数据新鲜度','processing':'等待连续两秒数据并检查滤波状态',
                 'ssh':'连接已配置服务器','gpu':'检查GPU处理worker与新鲜返回','model':'完成个人模型训练、校准与独立验证；保持LIVE锁定',
                 'unity_runtime':'启动当前构建并核对主线程运行健康','execution':'连接接收器并等待新鲜实际应用回执'}
        actions['execution_validation']='从最终Unity程序完成链路验收并生成当前发布快照'
        actions['quest']='使用真实Quest头显核验显示、XR交互和佩戴体验并记录硬件证据'
        actions['quest_build']='构建当前Android APK并核对产物及源码摘要'
        actions['quest_lan']='在同一可互通Wi-Fi运行当前Quest程序并等待新鲜回执'
        actions['quest_preview']='保持Quest应用前台并检查签名画面上传与2秒过期状态'
        result['blockers']=[dict(id=s['id'],label=s['label'],reason=s['summary'],next_action=actions[s['id']])
                            for s in stages if s['status']!='ok']
        software_ids={'unity_install','unity_build','training_software','execution_validation'}
        software_ready=all(s['status']=='ok' for s in stages if s['id'] in software_ids)
        formal=result['training']['formal_training_ready'] and all(s['status']=='ok' for s in stages if s['id'] in ['training_software','training_data','montage'])
        result['training']['formal_training_ready']=formal
        result.update(version=1,checked_at=iso(now),status='blocked' if result['blockers'] else 'ok',
                      software_ready=software_ready,formal_training_ready=formal,live_control_enabled=False)
        return result
