import argparse,json
from pathlib import Path
import numpy as np
from scipy.io import loadmat

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);a=ap.parse_args();p=Path(a.root)/'data/public/malta_eog/dataset1/extracted/DATASET';rows=[]
    for folder in sorted(p.glob('S*')):
        c=loadmat(folder/'ControlSignal.mat')['ControlSignal'].ravel();t=loadmat(folder/'TargetGA.mat')['TargetGA'];x=loadmat(folder/'EOG.mat')['EOG']
        edges=np.r_[0,np.flatnonzero(np.diff(c))+1,len(c)];runs=[{'value':int(c[a]),'start':int(a),'count':int(b-a)} for a,b in zip(edges[:-1],edges[1:])]
        rows.append({'subject':folder.name,'values':{str(k):int(v) for k,v in zip(*np.unique(c,return_counts=True))},'runs_by_value':{str(v):int(sum(z['value']==v for z in runs)) for v in np.unique(c)},'first_runs':runs[:10],'last_runs':runs[-5:],'return_angles_all_zero':bool(np.all(t[1::2]==0)),'target_shape':list(t.shape),'min_max':x.min(1).tolist()+x.max(1).tolist(),'nonfinite':int((~np.isfinite(x)).sum())})
    print(json.dumps(rows,default=lambda v:v.item()))

if __name__=='__main__':main()
