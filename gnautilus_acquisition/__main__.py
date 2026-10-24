import argparse
import json
import multiprocessing
from pathlib import Path
from . import __version__


def main():
    multiprocessing.freeze_support()
    import sys
    if sys.stdout and hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description='EEGMovement g.Nautilus participant acquisition')
    parser.add_argument('--version',action='store_true');parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--diagnose',action='store_true');parser.add_argument('--sdk-dir');parser.add_argument('--pairing')
    parser.add_argument('--serial',help='Select an exact g.Nautilus serial')
    parser.add_argument('--probe-stream',action='store_true',help='With --diagnose: briefly read an existing participant stream; no recording or forwarding')
    parser.add_argument('--probe-seconds',type=float,default=2.,help='Stream check duration, 0.1 to 10 seconds')
    parser.add_argument('--tls-smoke-test',action='store_true',help='Create the private-CA TLS context without SDK or network access')
    parser.add_argument('--ui-smoke-test',action='store_true',help='Render idle UI offscreen without SDK/network access')
    parser.add_argument('--audit',help='Read-only audit of a saved stream directory');parser.add_argument('--compare',help='Compare the same stream copied from the hub')
    parser.add_argument('--report-path',help='Write diagnosis/smoke result as UTF-8 JSON')
    parser.add_argument('--health-file',help='Installer startup receipt; device stays idle unless --start-receiving is explicit')
    parser.add_argument('--start-receiving',action='store_true',help='Explicitly begin participant reception after the GUI opens; never starts vendor acquisition or models')
    parser.add_argument('--status-file',help='Write bounded local GUI/runtime diagnostics without raw EEG or credentials')
    args=parser.parse_args()
    if args.start_receiving and any([args.diagnose,args.smoke_test,args.ui_smoke_test,args.audit,args.version,args.tls_smoke_test]):parser.error('--start-receiving is only valid for the normal GUI')
    if args.probe_stream and not args.diagnose:parser.error('--probe-stream requires --diagnose')
    if not .1<=args.probe_seconds<=10:parser.error('--probe-seconds must be between 0.1 and 10')
    if args.compare and not args.audit:parser.error('--compare requires --audit')
    def emit(value):
        content=json.dumps(value,ensure_ascii=False)
        if args.report_path:Path(args.report_path).write_text(content+'\n',encoding='utf-8')
        if sys.stdout:print(content)
    if args.version:emit(dict(version=__version__));return
    if args.audit:
        from .audit import audit_and_compare
        result=audit_and_compare(args.audit,args.compare);emit(result)
        raise SystemExit(0 if result['complete_closed'] and result.get('comparison_match',True) else 2)
    if args.ui_smoke_test:
        import os,tempfile
        os.environ['QT_QPA_PLATFORM']='offscreen'
        from PySide6 import QtCore,QtWidgets
        from .qt_gui import Workbench
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            window=Workbench(settings_path=Path(temp)/'settings.json');window.resize(1420,900);window.show();app.processEvents()
            capture=window.grab();dest=Path(args.report_path).with_suffix('.png') if args.report_path else None
            if dest:capture.save(str(dest))
            emit(dict(version=__version__,qt=QtCore.qVersion(),idle_rendered=not capture.isNull(),width=capture.width(),height=capture.height(),vendor_loaded=False,network_used=False))
            window.close()
        return
    if args.tls_smoke_test:
        import ssl
        from eeg_relay.trust import client_context
        from .updater import public_config
        context=client_context(public_config()['ca_pem'])
        emit(dict(version=__version__,tls_context_created=True,check_hostname=context.check_hostname,
                  certificate_required=context.verify_mode==ssl.CERT_REQUIRED,vendor_loaded=False,network_used=False))
        return
    if args.smoke_test:
        from .contract import validate_descriptor
        from .sdk import make_cdef
        import cffi,numpy
        from PySide6 import QtCore,QtWidgets
        emit(dict(version=__version__,platform='Windows x64',packaging_imports=True,vendor_loaded=False));return
    if args.diagnose:
        result=dict(version=__version__,hardware_verified=False)
        if args.pairing:
            from .client import connect_authenticated
            from .gui import load_enrollment
            try:s,_=connect_authenticated(load_enrollment(args.pairing));s.close();result['hub']='TLS and pairing accepted'
            except Exception as e:result['hub_error']=str(e)
        from .diagnostics import diagnose_sdk
        result.update(diagnose_sdk(args.sdk_dir,args.serial,probe_stream=args.probe_stream,seconds=args.probe_seconds))
        emit(result)
        raise SystemExit(0 if result['ok'] and 'hub_error' not in result else 2)
    from .gui import run_gui
    if sys.platform=='win32':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.restype=ctypes.c_void_p
        mutex=kernel.CreateMutexW(None,False,'Local\\EEGMovement.gNautilus.Workbench')
        if ctypes.get_last_error()==183:
            if not args.health_file:ctypes.windll.user32.MessageBoxW(None,'采集工作台已运行，请使用现有窗口。','EEGMovement',0x40)
            raise SystemExit(2)
    if args.health_file:
        import os
        os.environ['EEGMOVEMENT_STARTUP_HEALTH']=str(Path(args.health_file).resolve())
    run_gui(start_receiving=args.start_receiving,status_path=args.status_file)


if __name__=='__main__':main()
