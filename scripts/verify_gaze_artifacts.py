"""Read-only replay of all public gaze artifacts after an adapter update."""
import argparse,json,sys
from pathlib import Path
import numpy as np


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--evidence',required=True);a=ap.parse_args();root=Path(a.root)
    release=root/'releases'/(root/'current-release').read_text().strip();sys.path.insert(0,str(release))
    from gnautilus_ml.artifacts import load_artifact
    from gnautilus_ml.runner import compile_inputs
    evidence=json.loads(Path(a.evidence).read_text());results=[]
    for meta in evidence['models']:
        jid=meta['job_id'];job=next(j for j in evidence['jobs'] if j['id']==jid);protocol=evidence['protocols'][job['spec']['protocol_id']];dataset=json.loads((root/'datasets'/(job['spec']['dataset_id']+'.json')).read_text())
        roles,_=compile_inputs(dataset,protocol['window_s'],protocol['hop_s']);x,y,groups,ids=roles['development'];model,loaded=load_artifact(meta['path']);prediction=model.predict(x)
        with np.load(root/'logs'/(jid+'.development.predictions.npz'),allow_pickle=False) as old:
            if not np.array_equal(y,old['targets']) or not np.array_equal(prediction['labels'],old['labels']) or not np.array_equal(prediction['accepted'],old['accepted']):raise ValueError('Exported gaze identity/label/rejection changed: '+jid)
            error=float(np.max(np.abs(prediction['probabilities']-old['probabilities'])))
            if error>1e-6:raise ValueError('Gaze adapter prediction changed: '+jid)
        results.append(dict(model_id=meta['model_id'],job_id=jid,windows=len(x),maximum_probability_difference=error,labels_identical=True,rejection_identical=True))
    print(json.dumps(dict(release=str(release),models=results,training_started=False,production_model_activated=False)))


if __name__=='__main__':main()
