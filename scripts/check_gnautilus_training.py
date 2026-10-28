"""Read-only check. Fitting still requires the private server's checked entrypoint."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gnautilus_ml.common import read_json
from gnautilus_ml.governance import check_protocol
from gnautilus_ml.data import read_native

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--protocol',required=True);ap.add_argument('--dataset',required=True);ap.add_argument('--code-root',default=str(ROOT));ap.add_argument('--entrypoint');args=ap.parse_args()
    protocol=read_json(args.protocol);dataset=read_json(args.dataset);check_protocol(protocol,dataset,args.code_root)
    if args.entrypoint:
        p=Path(args.entrypoint).resolve();relative=str(p.relative_to(Path(args.code_root).resolve())).replace('\\','/')
        from gnautilus_ml.common import file_hash
        if protocol['code_sha256'].get(relative)!=file_hash(p):raise ValueError('Actual entrypoint is not bound in the checked protocol')
    for r in dataset.get('records',[]):
        if r.get('reader','gnautilus')=='gnautilus' and read_native(r['path'])['source_sha256']!=r['source_sha256']:raise ValueError('Actual data source changed')
        if r.get('reader','gnautilus')!='gnautilus':
            from gnautilus_ml.public_study import load_prepared
            load_prepared(r,protocol['window_s'],protocol['hop_s'])
    print(json.dumps({'checked':True,'fit_started':False,'records':len(dataset.get('records',[])),'complete_trials':len(dataset.get('episodes',[]))}))
