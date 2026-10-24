"""Fetch one publisher-hosted EOG research reference, never fit or relabel it."""
import argparse,hashlib,json,os,shutil,urllib.request,zipfile
from pathlib import Path
from datetime import datetime,timezone

SOURCES={'dataset1':'DATASET.zip','stationary':'Dataset_Stationary.zip','nonstationary':'Dataset_NonStationary.zip'}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',required=True);ap.add_argument('--dataset',choices=SOURCES,default='dataset1');a=ap.parse_args();r=Path(a.root)
    out=r/'data/public/malta_eog'/a.dataset;out.mkdir(parents=True,exist_ok=True)
    url='https://www.um.edu.mt/media/um/docs/centres/cbc/'+SOURCES[a.dataset];target=out/SOURCES[a.dataset]
    if not target.exists():
        if shutil.disk_usage(out).free<10*1024**3:raise ValueError('Insufficient free space for reference download')
        with urllib.request.urlopen(url,timeout=40) as response:
            declared=int(response.headers.get('Content-Length',0))
            if declared>1024**3:raise ValueError('Reference exceeds the one GiB investigation budget')
            count=0
            with target.with_suffix('.part').open('wb') as stream:
                while True:
                    b=response.read(1024**2)
                    if not b:break
                    stream.write(b);count+=len(b)
                    if count>1024**3:raise ValueError('Reference exceeds the investigation budget')
            if declared and declared!=count:raise ValueError('HTTP length mismatch')
        os.replace(target.with_suffix('.part'),target)
    with zipfile.ZipFile(target) as z:
        if z.testzip() is not None:raise ValueError('Downloaded publisher ZIP fails CRC')
        if sum(v.file_size for v in z.infolist())>3*1024**3:raise ValueError('Expanded source exceeds bound')
        for info in z.infolist():
            destination=(out/'extracted'/info.filename).resolve()
            if not destination.is_relative_to((out/'extracted').resolve()):raise ValueError('Unsafe source archive member')
        z.extractall(out/'extracted');names=z.namelist()
    receipt=dict(source=url,landing_page='https://www.um.edu.mt/cbc/ourprojects/eyecon/eogdataset/',retrieved_utc=datetime.now(timezone.utc).isoformat(),
        bytes=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),publisher_hash=None,zip_crc_valid=True,
        intended_use='native independent EOG methodology; not g.Nautilus scalp EEG or personal navigation',training_admission='pending source description, license, units, labels and grouping review',files=names)
    (out/'source-receipt.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt))


if __name__=='__main__':main()
