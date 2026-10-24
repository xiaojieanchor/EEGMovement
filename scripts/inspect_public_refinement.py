"""Read-only native eye-data structure and public-source availability checks."""
import argparse,csv,json,urllib.request,concurrent.futures
from pathlib import Path


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);a=ap.parse_args();root=Path(a.root)
    import mne
    p=root/'data/public/eegeyenet_example/openneuro-ds005872-v1.0.0/raw';out={}
    for f in p.rglob('*'):
        if f.is_file() and f.suffix in ['.json','.tsv'] and not f.name.endswith('.receipt.json'):
            if f.suffix=='.json':out[str(f.relative_to(p))]=json.loads(f.read_text())
            else:
                with f.open() as stream:
                    rd=csv.DictReader(stream,delimiter='\t');rows=[v for _,v in zip(range(3),rd)];out[str(f.relative_to(p))]={'columns':rd.fieldnames,'examples':rows}
    raw=mne.io.read_raw_edf(next(p.rglob('*.edf')),preload=False,verbose='ERROR')
    out['edf']={'channels':raw.ch_names,'types':raw.get_channel_types(),'rate':float(raw.info['sfreq']),'samples':int(raw.n_times),'duration_s':float(raw.n_times/raw.info['sfreq'])}
    endpoints=[
        'https://repo-prod.prod.sagebase.org/repo/v1/entity/syn64005218',
        'https://data.nemar.org/on007338/v1.0.0/participants.tsv',
        'https://data.nemar.org/on007338/v1.0.0/dataset_description.json',
        'https://api.osf.io/v2/nodes/ktv7m/files/osfstorage/',
        'https://osf.io/download/ge87t/',
    ]
    def fetch(url):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'EEGMovement source validation'}),timeout=20) as r:
                return dict(url=url,status=r.status,final_url=r.url,content_type=r.headers.get('Content-Type'),body=r.read(100000).decode('utf-8',errors='replace'))
        except Exception as ex:return dict(url=url,error=str(ex))
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:out['public_availability']=list(pool.map(fetch,endpoints))
    print(json.dumps(out,default=lambda v:v.item()))


if __name__=='__main__':main()
