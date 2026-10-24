"""Generate an auditable download snapshot without admitting data to training."""
import argparse
import csv
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil


DATASETS = {
    'kaya2018_halt': dict(name='Kaya2018 / HaLT', version='Figshare article v1',
        source='https://springernature.figshare.com/collections/A_large_electroencephalographic_motor_imagery_dataset_for_electroencephalographic_brain_computer_interfaces/3917698',
        license='CC0 (29 article records)', purpose='四肢运动想象方法验证',
        constraints='12人、29会话；保留左右手、左右腿、舌和passive原生标签，不改为私人导航。短提示任务须核对可用窗长。'),
    'bci_iv_2a': dict(name='BCI Competition IV-2a', version='original2008 GDF + published labels',
        source='https://www.bbci.de/competition/iv/desc_2a.pdf',
        license='BBCI original distribution: citation/attribution conditions in bci_iv_terms.html; BNCI equivalent separately declares CC BY-ND 4.0',
        purpose='CSP/多类方法与跨日对照',
        constraints='9人×2会话；22EEG+3EOG；双脚不能拆成左右腿；竞赛禁止EOG参与分类。'),
    'bci_iv_1': dict(name='BCI Competition IV Dataset 1', version='original2008 100Hz MAT + original 1000Hz labels',
        source='https://www.bbci.de/competition/iv/desc_1.html',
        license='BBCI original distribution citation/attribution conditions in bci_iv_terms.html',
        purpose='连续分类、无控制与停止研究',
        constraints='a/b/f/g为真人，c/d/e为人工；不混为生理EEG。100Hz数据与1000Hz原始真值不能直接按数组行配对；须建立显式时间对齐派生。'),
    'eegmmidb': dict(name='PhysioNet EEGMMIDB', version='1.0.0',
        source='https://physionet.org/content/eegmmidb/1.0.0/',
        license='Open Data Commons Attribution License v1.0 (ODC-By)',
        purpose='运动/想象来源对照与手部方法验证',
        constraints='109人、每人14个run；实动与想象分开，T1/T2语义随run改变，不提供独立左右腿。保留事件侧文件与原EDF。'),
    'openbmi_mi': dict(name='OpenBMI MI', version='GigaDB 100542 official current files; DOI 10.5524/100542',
        source='https://gigadb.org/dataset/100542',
        license='CC0 1.0 as recorded by DOI 10.5524/100542',
        purpose='左右手运动想象与跨日方法验证',
        constraints='只下MI：54人×2会话=108文件，不下载ERP/SSVEP。保留EEG_MI_train/test原分区；不是四肢或眼动融合数据。'),
    'eegeyenet_openneuro_example': dict(name='EEGEyeNet 单人BIDS示例', version='OpenNeuro ds005872 1.0.0',
        source='https://openneuro.org/datasets/ds005872/versions/1.0.0',
        license='CC0 as declared in this OpenNeuro version; independent from OSF full-corpus license',
        purpose='EEG/眼动/事件结构与读取核查',
        constraints='只有EP10一人一次dots示例；不等同356人原始整库或原NPZ基准，也不能用于跨人结论。'),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--plan', action='append', required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    now = datetime.now(timezone.utc).isoformat()
    datasets, plan_rows = {}, []
    for filename in args.plan:
        plan = json.loads(Path(filename).read_text())
        state_path = root / ('download-status-' + plan['id'] + '.json')
        state = json.loads(state_path.read_text()) if state_path.exists() else dict(items={})
        plan_rows.append(dict(id=plan['id'], plan_path=str(Path(filename).relative_to(root)),
                              plan_sha256=hashlib.sha256(Path(filename).read_bytes()).hexdigest(),
                              updated_utc=state.get('updated_utc'), completed_utc=state.get('completed_utc')))
        for item in plan['items']:
            key = item['dataset']
            data = datasets.setdefault(key, dict(**DATASETS[key], id=key, expected_files=0,
                expected_bytes=0, verified_files=0, verified_bytes=0, states=Counter(),
                errors=[], local_roots=set(), upstream_hash_files=0,
                training_admission='pending_protocol_and_content_review', role='unassigned',
                navigation_labels=False, training_started=False))
            data['expected_files'] += 1
            data['expected_bytes'] += item.get('bytes', 0)
            row = state['items'].get(item['relative_path'], dict(state='not_started'))
            data['states'][row['state']] += 1
            data['local_roots'].add('/'.join(item['relative_path'].split('/')[:2]))
            if row['state'] == 'verified':
                data['verified_files'] += 1
                data['verified_bytes'] += row['bytes']
                data['upstream_hash_files'] += int(bool(item.get('md5') or item.get('sha256')))
            if row.get('error'):
                data['errors'].append(dict(path=item['relative_path'], error=row['error']))
    inspection_path = root / 'content-inspection.json'
    inspection = json.loads(inspection_path.read_text()) if inspection_path.exists() else dict(files={})
    recordings = []
    for relative, row in inspection['files'].items():
        if row['status'] != 'readable':
            continue
        name = Path(relative).name
        key = row['dataset']
        fields = None
        if key == 'kaya2018_halt':
            match = re.match(r'HaLT-Subject([A-Z])-(\d{6})-', name)
            if match:
                fields = dict(subject=match[1], recording_date_code=match[2], session=name[:-4])
        elif key == 'bci_iv_1':
            match = re.fullmatch(r'BCICIV_(calib|eval)_ds1([a-g]).mat', name)
            if match:
                fields = dict(subject=match[2], original_partition=match[1],
                              signal_origin=row['content']['signal_origin'], session=None, recording=name[:-4])
        elif key == 'bci_iv_2a':
            match = re.fullmatch(r'(A\d\d)(T|E).gdf', name)
            if match:
                fields = dict(subject=match[1], original_partition=match[2], session=name[:-4])
        elif key == 'eegmmidb':
            match = re.fullmatch(r'(S\d{3})R(\d{2}).edf', name)
            if match:
                run = int(match[2])
                fields = dict(subject=match[1], run=run, session=None,
                              task_family='baseline' if run in [1,2] else 'imagined' if run in [4,6,8,10,12,14] else 'executed',
                              event_sidecar=relative + '.event')
        elif key == 'openbmi_mi':
            match = re.fullmatch(r'sess(\d+)_subj(\d+)_EEG_MI.mat', name)
            if match:
                fields = dict(subject=int(match[2]), session=int(match[1]), original_partitions=['EEG_MI_train','EEG_MI_test'])
        elif key == 'eegeyenet_openneuro_example' and name.endswith('_eeg.edf'):
            fields = dict(subject='EP10', session='01', run='01', task='dots', example_only=True)
        if fields is not None:
            sample_rate = row['content'].get('sample_rate_hz')
            if row['content'].get('format') == 'EDF':
                signal_rates = {rate for label,rate in zip(row['content']['channel_labels'], row['content']['sample_rates']) if label != 'EDF Annotations'}
                sample_rate = next(iter(signal_rates)) if len(signal_rates) == 1 else None
            recordings.append(dict(dataset=key, path=relative, sha256=row['identity']['sha256'],
                                   calendar_date=None, role='unassigned', training_admission='pending_protocol_and_content_review',
                                   source_sample_rate_hz=sample_rate,
                                   **fields))
    recordings.sort(key=lambda r:(r['dataset'], str(r['subject']).zfill(4),
                                   r.get('recording_date_code') or '', str(r.get('session') or ''),
                                   r.get('run') or 0, r['path']))
    (root / 'recording-index.json').write_text(json.dumps(dict(updated_utc=now, files=recordings,
        field_source='Native filenames/headers and official source descriptions; no private navigation relabeling',
        training_started=False), ensure_ascii=False, indent=2), encoding='utf-8')
    with (root / 'recording-index.csv').open('w', encoding='utf-8-sig', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=['dataset','subject','session','run','recording_date_code','calendar_date',
            'original_partition','task_family','signal_origin','source_sample_rate_hz','path','sha256','role','training_admission'], extrasaction='ignore')
        writer.writeheader()
        writer.writerows(recordings)
    verification_path = root / 'independent-file-verification.json'
    verification = json.loads(verification_path.read_text()) if verification_path.exists() else None
    for key, data in datasets.items():
        data['states'] = dict(data['states'])
        data['local_roots'] = sorted(data['local_roots'])
        data['download_status'] = 'complete_verified' if data['verified_files'] == data['expected_files'] else 'failed_or_partial' if data['errors'] else 'downloading'
        rows = [dict(path=k, **v) for k, v in inspection['files'].items() if v['dataset'] == key]
        data['structure_readable_files'] = sum(v['status'] == 'readable' for v in rows)
        data['structure_errors'] = [dict(path=v['path'], error=v['error']) for v in rows if v['status'] == 'error']
        records = [r for r in recordings if r['dataset'] == key]
        data['recording_files'] = len(records)
        data['subjects_in_inspected_recordings'] = sorted(set(str(r['subject']) for r in records))
        data['observed_sample_rate_counts'] = dict(Counter(str(r['source_sample_rate_hz']) for r in records if r['source_sample_rate_hz'] is not None))
        if key == 'eegmmidb':
            data['non_160hz_records'] = [dict(path=r['path'], subject=r['subject'], run=r['run'], sample_rate_hz=r['source_sample_rate_hz']) for r in records if r['source_sample_rate_hz'] != 160]
    blockers = [dict(id='eegeyenet_full', name='EEGEyeNet 原始整库 / 基准NPZ', status='blocked_official_download',
        source='https://osf.io/ktv7m/',
        evidence=['diagnostics/eegeyenet-availability.json', '_sources/20261023/eye-ge87t.json', '_sources/20261023/eye_dots.txt', '_sources/20261023/eye_min.txt', '_sources/20261023/eye_max.txt', '_sources/20261023/eye_license_file.pdf'],
        reason='旧官方ge87t下载返回404；当前Google Drive数据目录列举为空。未获取整库。项目附license.pdf为CC BY-NC-SA 4.0，目录另有GPL3 LICENSE，须按实际版本区分；不能套用示例CC0或代码许可。',
        training_admission='not_admitted', training_started=False)]
    report = dict(schema='eeg.public_download_catalog.v1', updated_utc=now,
                  server=dict(host='172.25.56.83', ssh_port=5522, root=str(root)),
                  free_bytes=shutil.disk_usage(root).free, training_started=False,
                  datasets=list(datasets.values()), blocked=blockers, plans=plan_rows,
                  independent_full_file_audit={k:v for k,v in verification.items() if k != 'files'} if verification else None,
                  structure_scope='Header/shape/marker reading only; not electrical QC, label provenance acceptance, train/test protocol acceptance, or model accuracy.')
    temporary = root / 'public-catalog.json.tmp'
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(root / 'public-catalog.json')
    lines = ['# 公共EEG下载与校验记录', '', f'快照时间（UTC）：{now}。服务器 `172.25.56.83:5522`，根目录 `{root}`。', '',
        '本轮仅下载、解包和只读结构核查。未启动训练、未更换模型；下载完成不自动授予训练或独立测试资格。', '',
        '| 数据 | 已校验／计划文件 | 已校验／计划GB（十进制） | 状态 |', '|---|---:|---:|---|']
    for data in datasets.values():
        status = '全部下载并校验' if data['download_status'] == 'complete_verified' else '含失败或部分记录' if data['errors'] else '下载中'
        lines.append(f"|{data['name']}|{data['verified_files']} / {data['expected_files']}|{data['verified_bytes']/1e9:.3f} / {data['expected_bytes']/1e9:.3f}|{status}|")
    lines += ['', f"已校验原始文件合计 **{sum(d['verified_bytes'] for d in datasets.values()):,} bytes**；不含解包副本、日志、未完成part和来源材料。", '',
        '每个原始文件旁保存 `.receipt.json`，记录来源URL、原始摘要（若提供）、本地SHA256、字节数及UTC。ZIP另做CRC核验并保留原包；解包文件保留父归档SHA256。PhysioNet用官方SHA256逐文件核对；HaLT用官方MD5核对并另算SHA256。OpenBMI官方目录未给MD5，按官方大小、MAT结构及本地SHA256记录，不冒称有发布方摘要。', '',
        'EEGEyeNet OpenNeuro示例固定1.0.0 Git树；大文件核对annex SHA256，小文件保留固定版本Git blob身份与下载摘要。', '',
        '## 来源与用途边界', '']
    for data in datasets.values():
        lines += [f"### {data['name']}", '', f"来源：[{data['version']}]({data['source']})。", '',
            f"许可记录：{data['license']}。", '', f"目录：`{', '.join(data['local_roots'])}`。", '',
            f"用途：{data['purpose']}。{data['constraints']}", '',
            f"只读结构核查：{data['structure_readable_files']}个文件可读，{len(data['structure_errors'])}个错误。侧文件可读只代表存在，不代表标签内容或信号质量合格。", '']
    lines += ['## 原文件只读核查发现', '',
        '- HaLT：本次29份MAT的`o.sampFreq=200`、22列、`binsuV=1`；`o.chnames`末路实际为X5，而通用说明写X3。保留原名，A1/A2及X类不自动视为19路头皮EEG，不自行改原始数组或重复缩放。',
        '- BCI IV-2a：GDF共25条生理信号，检查时明确将末3路标记为EOG。部分EEG名是`EEG-0`等原始标记，后续需按官方蒙太奇显式映射，不能按名字猜头皮位置。',
        '- PhysioNet：EDF容器含64路信号及EDF Annotations记录。注释记录的采样率不是EEG采样率，不能把容器通道数直接当成65路EEG。头文件预滤波字段仅原样记录，不据其空值或0Hz断言设备没有滤波。',
        '- PhysioNet全量核查发现36份EDF头文件将EEG采样率标为128Hz（S088、S092、S100各12份），其余1490份标为160Hz。具体文件在`public-catalog.json`的`non_160hz_records`及`recording-index`中列出；训练前须单独核对时长、事件与采样身份，不能把整库强行按160Hz解释，也不在本轮静默重采样。',
        '- OpenBMI：抽查一份已完成MI文件的训练结构，`x`为样本×62通道，`smt`为4000×100×62；`fs=1000`，`class`实际1=right、2=left，另有EMG和前后静息字段。该抽查不代表108份全部已完成语义/电气质量核查；逐文件顶层MAT结构另存索引。',
        '- 标签、记录长度、伪迹、完整会话与独立日期划分仍须在正式训练任务中审查；“可读”不等于“信号可用”或“标签正确”。', '',
        '## 独立复核', '']
    if verification:
        lines += [f"全文件SHA256再次读取：{verification['verified_files']} / {verification['expected_files']}通过，{verification['failed_files']}失败；{verification['verified_bytes']:,} bytes。复核UTC：{verification['updated_utc']}。详见`independent-file-verification.json`。", '']
    else:
        lines += ['独立全文件复核尚未生成；当前表格表示下载器完成的校验回执。', '']
    lines += ['## 未下载成功的整库', '', blockers[0]['reason'], '',
        '服务器端再次检查的HTTP状态、UTC和官方目录文件数见[可用性检查](diagnostics/eegeyenet-availability.json)。', '',
        '## 已发现的来源问题', '',
        '- OpenBMI官方文件清单将会话1受试者26的展示名写成`.mat6`，真实下载URL为`.mat`；按真实URL保留文件名，原始清单保存在`_sources/20261023/openbmi-files.json`。',
        '- OpenBMI README在目录里的字节数与本次官方响应不一致，保留实际响应及原目录，不将该目录大小当作数据完整性的充分证据。',
        '- BCI IV-1真值包README记录受试者a早期1000Hz评价数据曾被截短；本轮保留该说明。100Hz数据与1000Hz标签的对齐需另建派生，不能隐式截断。',
        '- BCI IV-1真实/人工划分依据[官方最终结果](https://www.bbci.de/competition/iv/results/index.html)：a/b/f/g为真人，c/d/e为人工。原文件保持同包，索引分开身份，不把人工数据算作真实EEG。', '',
        '## 继续、核查与使用', '',
        '下载器仅处理显式清单，保留断点文件；再次运行相同清单会核对已完成文件并续传。每份计划使用独立进程锁，不允许同一计划重复运行。TLS保持证书验证；未开放服务器入站端口。', '',
        '```bash', f'cd {root}',
        'python3 download_public_eeg.py --root "$PWD" --plan openbmi-plan.json --workers 12',
        'python3 download_public_eeg.py --root "$PWD" --plan physio-plan.json --workers 12', '```', '',
        '应先确认相同计划没有活动进程再手动续传。当前进度以`download-status-<计划ID>.json`为准；`public-catalog.json`及本文是生成时快照。', '',
        '本轮有效计划仅为`core-plan.json`、`physio-plan.json`、`openbmi-plan.json`和`eye-example-plan.json`。`download-plan.json`、`download-plan-v2.json`及`halt-direct-plan.json`是排障历史，不应再次启动；早期未完成ZIP或part不计入完成数量。', '',
        '下一步须逐库建立数据合同、原生标签、单位/参考、完整会话分组和用途许可回执，再通过训练治理。所有角色保持unassigned；UNKNOWN不补REST，不补假通道凑32路。', '',
        '索引：`original-file-index.json`（原始文件及回执）、`content-inspection.json`（只读结构结果）、`recording-index.json`及同名CSV（原生受试者/会话/run/分区，训练角色未分配）、`_sources/20261023/source-index.json`（来源快照SHA256）、各数据目录`extraction-index.json`（解包追溯）。', '']
    (root / '公开数据下载清单与状态.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(dict(datasets={k:dict(status=d['download_status'], verified=d['verified_files'], expected=d['expected_files']) for k,d in datasets.items()}, training_started=False)))


if __name__ == '__main__':
    main()
