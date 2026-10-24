"""Run three immutable source/QC ablations on CPU, without starting any fit."""
import argparse,json,os,subprocess
from pathlib import Path


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);a=ap.parse_args();r=Path(a.root)
    release=r/'releases'/(r/'current-release').read_text().strip()
    env={**os.environ,'PYTHONPATH':str(release),'CUDA_VISIBLE_DEVICES':'','OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2'}
    for tag in ['pre7','full19','pre19']:
        x=subprocess.run([str(r/'.venv/bin/python'),'-m','gnautilus_ml.public_study','--root',str(r),'--plan',str(r/('context-'+tag+'-plan.json')),'--only','halt'],env=env,capture_output=True,text=True)
        print(json.dumps({'tag':tag,'exit':x.returncode,'stdout':x.stdout[-1200:],'stderr':x.stderr[-2000:]}),flush=True)
        if x.returncode:raise SystemExit(x.returncode)


if __name__=='__main__':main()
