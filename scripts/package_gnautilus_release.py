"""Package verified binaries without SDK DLLs, enrollment files or user recordings."""
from pathlib import Path
import hashlib,importlib.metadata,json,os,shutil,sys,zipfile

ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/'docs/gnautilus/integration_20261022';VERIFY=DOC/'verification'

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda:f.read(1024*1024),b''):h.update(data)
    return h.hexdigest()

def zip_files(dest,files):
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for source,name in files:z.write(source,name)
    with zipfile.ZipFile(dest) as z:
        if z.testzip():raise ValueError('ZIP CRC error')
    return dict(path=str(dest),bytes=dest.stat().st_size,sha256=sha(dest),crc_verified=True)

def main():
    build=json.loads((VERIFY/'acquisition-build.json').read_text('utf-8'))
    version=json.loads((VERIFY/'frozen-smoke.json').read_text('utf-8'))['version']
    if not build.get('success'):raise ValueError('Successful acquisition build required')
    for name,digest in build['source_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('Source changed after freeze: '+name)
    exe=Path(build['exe']);folder=exe.parent
    if sha(exe)!=build['exe_sha256']:raise ValueError('EXE hash mismatch')
    readme=(DOC/'独立采集工作台使用说明.md').read_text('utf-8')
    readme=readme.replace('connection_fix_v101/连接问题分析与修复.md','连接问题与配对说明.md')
    (folder/'先读我.md').write_text(readme,encoding='utf-8')
    shutil.copy2(DOC/'connection_fix_v101/连接问题分析与修复.md',folder/'连接问题与配对说明.md')
    shutil.copy2(DOC/'Python API与独立采集端接入说明_20261022.pdf',folder/'Python API与独立采集端接入说明.pdf')
    template=folder/'templates';template.mkdir(exist_ok=True)
    (template/'layout-unverified.json').write_text(json.dumps(dict(verified=False,channels={},reference=None,ground=None,wearing_id=None),ensure_ascii=False,indent=2),encoding='utf-8')
    (template/'实际位置填写说明.txt').write_text('默认位置未知。只有逐个核实实际物理输入后才将verified改为true，并填写所有启用输入。channels的键是从0开始的物理输入序号字符串，例如输入1使用键"0"；值是实际位置。reference、ground、wearing_id未知保持null。此文件不配置设备。\n',encoding='utf-8')
    command='@echo off\r\nset "taskLog=%LOCALAPPDATA%\\EEGMovement\\gNautilus"\r\nif not exist "%taskLog%" mkdir "%taskLog%"\r\nstart /wait "" "%~dp0EEGMovement-gNautilus.exe" --smoke-test --report-path "%taskLog%\\dependencies.json"\r\ntype "%taskLog%\\dependencies.json"\r\npause\r\n'
    (folder/'依赖自检.cmd').write_bytes(command.encode('ascii'))
    licenses=folder/'third-party-licenses';licenses.mkdir(exist_ok=True);versions={}
    for name in ['numpy','cffi','pywin32','PySide6','PySide6_Essentials','PySide6_Addons','shiboken6','pyinstaller']:
        dist=importlib.metadata.distribution(name);versions[name]=dist.version
        for file in dist.files or []:
            text=str(file).replace('\\','/')
            if 'licenses/' in text.lower() or Path(text).name.lower() in ['license','license.txt','copying','copying.txt']:
                source=Path(dist.locate_file(file))
                if source.is_file():
                    target=licenses/(name+'-'+source.name);shutil.copy2(source,target)
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():shutil.copy2(python_license,licenses/'Python-LICENSE.txt')
    (licenses/'说明.txt').write_text('本目录保留所用已安装依赖随附的许可文件。界面字体从目标Windows系统读取，没有打包微软字体。Qt使用动态库；原厂GDS驱动、服务、头文件与DLL由设备电脑的原厂安装提供，未随本包分发。\n',encoding='utf-8')
    source_files=list((ROOT/'gnautilus_acquisition').glob('*.py'))+[ROOT/'gnautilus_acquisition/requirements-build.txt',ROOT/'scripts/gnautilus_entry.py',ROOT/'scripts/build_gnautilus_acquisition.py']
    source_files += [ROOT/'tests'/name for name in ['test_gnautilus_transport.py','test_gnautilus_sdk.py','test_gnautilus_gui.py','test_gnautilus_display.py','test_gnautilus_qt.py','test_gnautilus_audit.py','test_gnautilus_connection.py']]
    for p in source_files:
        target=folder/'source'/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    (folder/'source/tests/__init__.py').touch()
    proof=folder/'source/docs/gnautilus/integration_20261022/verification';proof.mkdir(parents=True,exist_ok=True)
    for name in ['acquisition-build.json','frozen-smoke.json','frozen-worker.json','frozen-ui.json','frozen-audit.json']:shutil.copy2(VERIFY/name,proof/name)
    (folder/'source/README.md').write_text('独立采集端源码。项目根目录中的中枢和Unity更新另行保留。使用Python 3.13 x64安装gnautilus_acquisition/requirements-build.txt后，可运行python -m gnautilus_acquisition；构建为python scripts/build_gnautilus_acquisition.py。SDK测试还需项目原有厂商头文件；不在此包中分发。该源码允许重新构建或替换动态Qt依赖，完整软件链路测试在原项目tests目录。\n',encoding='utf-8')
    info=dict(schema='gnautilus.portable.v1',version=version,date='2026-10-22',source_sha256=build['source_sha256'],exe_sha256=build['exe_sha256'],dependencies=versions,
              hardware_verified=False,network_verified='localhost software only',model_state='not_configured',vendor_dlls_bundled=False)
    (folder/'build-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    paths=sorted(p for p in folder.rglob('*') if p.is_file() and p.name!='FILES_SHA256.json')
    forbidden={'gdsclientapi.dll','gdsserver.dll','agents.json','server.key','quest_enrollment.json'}
    if any(p.name.lower() in forbidden for p in paths):raise ValueError('Private/vendor file in release')
    manifest={p.relative_to(folder).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p)) for p in paths}
    (folder/'FILES_SHA256.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    paths=sorted(p for p in folder.rglob('*') if p.is_file())
    output=folder.parent
    portable=zip_files(output/f'EEGMovement-gNautilus-Windows-x64-v{version}-20261022.zip',[(p,(Path(folder.name)/p.relative_to(folder)).as_posix()) for p in paths])
    source=zip_files(output/f'EEGMovement-gNautilus-Source-v{version}-20261022.zip',[(p,p.relative_to(folder/'source').as_posix()) for p in (folder/'source').rglob('*') if p.is_file()])
    vr=[]
    for name in ['android-build.json','windows-build.json']:
        record=json.loads((VERIFY/name).read_text('utf-8-sig'));binary=Path(record['output'])
        if not record.get('success') or sha(binary)!=record['output_sha256']:raise ValueError('VR build mismatch')
        vr.append(dict(platform=record['platform'],build_id=record['build_id'],path=str(binary),sha256=sha(binary),bytes=binary.stat().st_size))
        if name=='windows-build.json':
            base=binary.parent
            for relative,digest in record['artifact_hashes'].items():
                if sha(base/relative)!=digest:raise ValueError('VR artifact mismatch: '+relative)
            windows_zip=zip_files(base.parent/'EEGMovement-Windows-20261022.zip',[(p,(Path('Windows')/p.relative_to(base)).as_posix()) for p in base.rglob('*') if p.is_file()])
    result=dict(portable=portable,source=source,windows_vr=windows_zip,vr=vr,portable_files=len(paths),portable_uncompressed_bytes=sum(p.stat().st_size for p in paths),all_source_hashes_match=True,all_vr_artifacts_match=True,no_vendor_or_enrollment_files=True)
    (VERIFY/'packages.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
