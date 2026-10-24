"""Only metadata inspection; deliberately no acquisition, training or service CLI."""
import argparse
from dataclasses import fields
import json
from pathlib import Path
from .contracts import RecordingIdentity,validate_identity,dataset_admission

def main():
    parser=argparse.ArgumentParser(description='Offline g.Nautilus research contracts; no EEG runner')
    parser.add_argument('operation',choices=['capabilities','check-record','check-registry'])
    parser.add_argument('path',nargs='?');args=parser.parse_args()
    if args.operation=='capabilities':
        print(json.dumps({'offline_math':True,'device_connected':False,'real_eeg_training':False,
          'inference_service':False,'vr_execution':False,'needs':'actual API plus new locked governance and hardware acceptance'},ensure_ascii=False));return
    if not args.path:parser.error('path required')
    source=json.loads(Path(args.path).read_text('utf-8'))
    if args.operation=='check-record':
        try:validate_identity(RecordingIdentity(**source))
        except (ValueError,TypeError) as e:
            print(json.dumps({'contract_valid':False,'reason':str(e)},ensure_ascii=False));raise SystemExit(2)
        print(json.dumps({'contract_valid':True,'hardware_verified':False,'training_permission':False}));return
    rows=source.get('datasets',[])
    if not rows:parser.error('nonempty datasets registry required')
    print(json.dumps({'datasets':[{'id':r['id'],**dataset_admission(r)} for r in rows],
                      'effect':'inspection_only_no_admission_state_change'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
