"""Prepare non-secret local defaults. Does not connect to servers or devices."""
import argparse
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role',choices=['acquisition','hub','all'],default='acquisition')
    args=parser.parse_args()
    for name in ['hub/runtime','outputs','data/gnautilus']:(ROOT/name).mkdir(parents=True,exist_ok=True)
    if args.role in ['hub','all']:
        target=ROOT/'configs/gnautilus_compute.json'
        if not target.exists():shutil.copyfile(ROOT/'configs/gnautilus_compute.example.json',target)
    print(json.dumps(dict(role=args.role,device_started=False,network_contacted=False,
        next='python -m gnautilus_acquisition' if args.role=='acquisition' else 'python scripts/start_hub.py',
        compute_configuration='Fill configs/gnautilus_compute.json locally; never store passwords' if args.role!='acquisition' else 'Not needed for acquisition'),ensure_ascii=False))


if __name__=='__main__':main()
