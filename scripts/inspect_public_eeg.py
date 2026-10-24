"""Read downloaded public originals and build a download-only provenance catalog.

Linux compute server entry point. Never trains, registers production datasets,
changes labels, executes archive contents, or imports the model service.
"""
import argparse
import datetime
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def extract_original(archive, receipt):
    """Extract into a separate immutable directory, retaining archive ancestry."""
    target = archive.parent.parent / 'extracted' / archive.stem
    index_path = target / 'extraction-index.json'
    if index_path.exists():
        index = json.loads(index_path.read_text())
        if index['archive_sha256'] != receipt['sha256']:
            raise ValueError('Extraction ancestry changed')
        if all((target / r['name']).stat().st_size == r['bytes'] for r in index['files']):
            return index
        raise ValueError('Existing extracted file is missing or changed size')
    rows = []
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        if len(infos) > 20000 or sum(v.file_size for v in infos) > 100 * 1024**3:
            raise ValueError('Archive exceeds extraction limits')
        for entry in infos:
            name = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if name.is_absolute() or '..' in name.parts or '\\' in entry.filename or stat.S_ISLNK(mode):
                raise ValueError('Unsafe archive member: ' + entry.filename)
            destination = (target / entry.filename).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise ValueError('Archive member outside extraction root')
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + '.extracting')
            h = hashlib.sha256()
            with source.open(entry) as src, temporary.open('wb') as out:
                for block in iter(lambda: src.read(4 * 1024**2), b''):
                    h.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if temporary.stat().st_size != entry.file_size:
                raise ValueError('Extracted length mismatch')
            if destination.exists():
                if sha256(destination) != h.hexdigest():
                    raise ValueError('Existing extracted content conflict')
                temporary.unlink()
            else:
                os.replace(temporary, destination)
            rows.append(dict(name=entry.filename, bytes=entry.file_size, sha256=h.hexdigest(), crc32=f'{entry.CRC:08x}'))
    index = dict(archive=str(archive), archive_sha256=receipt['sha256'], files=rows,
                 extracted_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    atomic(index_path, index)
    return index


def edf_header(path):
    with path.open('rb') as source:
        fixed = source.read(256)
        if fixed[:8] != b'0       ':
            raise ValueError('Not an EDF version 0 file')
        header_bytes = int(fixed[184:192])
        records = int(fixed[236:244])
        duration = float(fixed[244:252])
        channels = int(fixed[252:256])
        if not 0 < channels < 1000 or duration <= 0 or header_bytes != 256 * (channels + 1):
            raise ValueError('Invalid EDF header dimensions')
        rest = source.read(header_bytes - 256)
    fields = {}
    offset = 0
    for key, width in [('labels', 16), ('transducer', 80), ('units', 8),
                       ('physical_min', 8), ('physical_max', 8), ('digital_min', 8),
                       ('digital_max', 8), ('prefilter', 80), ('samples_per_record', 8), ('reserved', 32)]:
        fields[key] = [rest[offset + i * width:offset + (i + 1) * width].decode('ascii').strip() for i in range(channels)]
        offset += channels * width
    expected = header_bytes + records * 2 * sum(int(x) for x in fields['samples_per_record'])
    if records < 0 or expected != path.stat().st_size:
        raise ValueError('EDF record count/length mismatch')
    return dict(format='EDF', samples=records * int(fields['samples_per_record'][0]),
                channels=channels, channel_labels=fields['labels'], units=fields['units'],
                sample_rates=[int(x) / duration for x in fields['samples_per_record']],
                prefilter=fields['prefilter'], records=records, duration_seconds=records * duration)


def mat_header(path, dataset):
    import scipy.io as sio
    import numpy as np
    result = dict(format='MAT', variables=[dict(name=n, shape=list(s), matlab_type=t) for n, s, t in sio.whosmat(path)])
    if dataset == 'kaya2018_halt':
        o = sio.loadmat(path, simplify_cells=True)['o']
        result.update(sample_rate_hz=int(o['sampFreq']), samples=int(o['nS']),
                      data_shape=list(o['data'].shape), channel_labels=[str(v) for v in o['chnames']],
                      binsuV=float(o['binsuV']), marker_counts={str(k):int(v) for k,v in zip(*np.unique(o['marker'], return_counts=True))})
        if o['data'].shape != (o['nS'], len(o['chnames'])) or len(o['marker']) != o['nS']:
            raise ValueError('HaLT sample/channel/marker dimensions do not agree')
    elif dataset == 'bci_iv_1':
        data = sio.loadmat(path, variable_names=['nfo', 'mrk', 'true_y'], simplify_cells=True)
        if 'nfo' in data:
            nfo = data['nfo']
            result.update(sample_rate_hz=int(nfo['fs']), channel_labels=[str(v) for v in nfo['clab']],
                          native_classes=[str(v) for v in nfo['classes']], scale_to_uV=0.1)
        if 'true_y' in data:
            result.update(label_shape=list(np.shape(data['true_y'])), label_values=[str(v) for v in np.unique(data['true_y'])])
        subject = path.stem.split('ds1')[-1][0]
        result['subject'] = subject
        result['signal_origin'] = 'artificial' if subject in 'cde' else 'human' if subject in 'abfg' else 'unknown'
        if 'true_y' in path.name:
            result['label_sample_rate_hz'] = 1000
            result['requires_explicit_alignment_to_100hz'] = True
    elif dataset == 'bci_iv_2a' and path.parent.name == 'true_labels':
        data = sio.loadmat(path, simplify_cells=True)
        if 'classlabel' in data:
            result.update(label_count=int(np.size(data['classlabel'])), label_values=[int(v) for v in np.unique(data['classlabel'])])
    return result


def gdf_header(path):
    import mne
    raw = mne.io.read_raw_gdf(path, eog=[22, 23, 24], preload=False, verbose='ERROR')
    try:
        return dict(format='GDF', sample_rate_hz=float(raw.info['sfreq']), samples=int(raw.n_times),
                    channel_labels=raw.ch_names, channel_types=raw.get_channel_types(),
                    annotation_count=len(raw.annotations), annotation_types=sorted(set(raw.annotations.description)))
    finally:
        raw.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--plan', action='append', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    cache_path = root / 'content-inspection.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {'files': {}}
    inventory = []
    for plan_path in args.plan:
        plan = json.loads(Path(plan_path).read_text())
        for item in plan['items']:
            path = (root / item['relative_path']).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Plan path outside root')
            receipt_path = path.with_name(path.name + '.receipt.json')
            if not path.exists() or not receipt_path.exists():
                continue
            receipt = json.loads(receipt_path.read_text())
            if path.stat().st_size != receipt['bytes']:
                raise ValueError('Original length changed after receipt')
            inventory.append(receipt)
            paths = [(path, receipt['sha256'])]
            if path.suffix.lower() == '.zip':
                index = extract_original(path, receipt)
                target = path.parent.parent / 'extracted' / path.stem
                paths = [(target / row['name'], row['sha256']) for row in index['files']]
            for candidate, digest in paths:
                key = str(candidate.relative_to(root))
                identity = dict(sha256=digest, bytes=candidate.stat().st_size, mtime_ns=candidate.stat().st_mtime_ns)
                if cache['files'].get(key, {}).get('identity') == identity:
                    continue
                try:
                    if candidate.suffix.lower() == '.edf':
                        content = edf_header(candidate)
                    elif candidate.suffix.lower() == '.mat':
                        content = mat_header(candidate, item['dataset'])
                    elif candidate.suffix.lower() == '.gdf':
                        content = gdf_header(candidate)
                    else:
                        content = dict(format='sidecar', structure_check='not_applicable')
                    cache['files'][key] = dict(identity=identity, dataset=item['dataset'], status='readable', content=content)
                except Exception as error:
                    cache['files'][key] = dict(identity=identity, dataset=item['dataset'], status='error', error=type(error).__name__ + ': ' + str(error))
    cache.update(updated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 training_admission='pending_protocol_and_content_review', training_started=False,
                 bci_iv_1_real_subjects=['a','b','f','g'], bci_iv_1_artificial_subjects=['c','d','e'],
                 bci_iv_1_origin_source='https://www.bbci.de/competition/iv/results/index.html')
    atomic(cache_path, cache)
    atomic(root / 'original-file-index.json', dict(updated_utc=cache['updated_utc'], files=inventory, training_started=False))
    print(json.dumps(dict(originals=len(inventory), content_entries=len(cache['files']),
                          content_errors=[k for k,v in cache['files'].items() if v['status']=='error'], training_started=False)))


if __name__ == '__main__':
    main()
