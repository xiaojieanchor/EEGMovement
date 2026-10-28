"""Bind hybrid exports to verified native descriptors, not an optional parent head."""
from pathlib import Path
from .common import file_hash, read_json, within
from .navigation_contract import CHANNELS
from .stream_runtime import contract_from_descriptor


def native_export_metadata(dataset):
    review=dataset.get('contract_review',{})
    if dataset.get('channels')!=CHANNELS or 'User confirmed all 32 positions and swap16/19' not in review.get('attestation','') or review.get('original_descriptors_unchanged') is not True:
        raise ValueError('Explicitly confirmed physical montage required for export')
    root=Path(dataset['folder']);receipt=dataset['source_receipt']['files'];sources=[];contract=None
    streams=sorted({e['stream_id'] for e in dataset['episodes'] if e['contract_id'].startswith('native_')})
    if not streams:raise ValueError('No native descriptor contract available')
    for stream in streams:
        rel='.metadata/canonical/'+stream+'/descriptor.original.json';path=within(root/rel,root)
        if receipt.get(rel)!=file_hash(path):raise ValueError('Original descriptor hash differs')
        current=contract_from_descriptor(read_json(path))
        if contract is not None and current!=contract:raise ValueError('Native input contracts differ; export requires an explicit compatible source policy')
        contract=current;sources.append(dict(path=rel,sha256=receipt[rel],stream_id=stream))
    return dict(input_contract=contract,montage_attestation=dict(channels=CHANNELS,source='explicit_user_confirmation',swap16_19=True,reference_site_verified=False,ground_site_verified=False),
                input_provenance=dict(schema='gnautilus.hybrid_input_provenance.v1',descriptors=sources,physical_columns_unchanged=True,official_source_contract_merged=False))
