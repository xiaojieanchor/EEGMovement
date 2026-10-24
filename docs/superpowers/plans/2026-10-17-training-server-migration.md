# Training Server Migration Implementation Plan

> Execution: inline with superpowers:executing-plans. Perform each phase with evidence before cutover.

**Goal:** Copy EEGMovement model checkpoints and training logs from A100-1 to A100-2, then make A100-2 the future training destination.

**Architecture:** Source synv01 192.168.1.191, external SSH 172.25.56.83:3322; target nvtest02 192.168.1.196, external SSH 172.25.56.83:5522. Transfer bulk files directly over LAN SSH/rsync, pinning the target key. Preserve /home/sysu/EEGMovement paths and all source originals until validated. Use a project-private runtime and do not change unrelated workloads.

**Spec:** User request and screenshot in this chat; user explicitly requests moving training logs/data and all future training to A100-2, with direct LAN transfer.

## Constraints and discovered blockers

- Credentials only in hidden input/memory; never put account passwords in scripts/config/logs.
- Root project currently occupies 435748134912 allocated bytes; target has only 104702464000 available bytes. Full raw archive migration requires added storage. User has been asked for expansion or a storage path; no unrelated cleanup is authorized.
- Target NVML 580.178 mismatches loaded kernel 580.173.02. Diagnose project-private remedies before any global driver change/reboot.
- No source training jobs are running; existing project inference workers and unrelated jobs remain intact.
- Historical paths, reports and raw datasets preserve identity. Runtime PID/token files must not be treated as live target processes.

## Review focus

- An incomplete data copy must never be reported as complete or used to silently start a full corpus training job.
- Existing source-host credentials/pointers must not be silently reused as if they identify the new machine.
- A copied venv with external .pth links is not a portable training environment.
- Source inference, data collection and unrelated GPU jobs must remain available during migration.
- Temporary transfer keys must be removed after the transfer or narrowly restricted while a storage-dependent copy is pending.

## Tasks

- [x] Inspect both hosts, keys, routes, disk, GPU and project processes.
- [x] Verify direct source-to-target LAN SSH key matches target external entry point.
- [ ] Prepare target project and restricted temporary transfer authentication.
- [ ] Copy code, models, training history/checkpoints, training run artifacts directly by LAN (excluding sessions and all data directories); verify hashes.
- [x] User narrowed scope: copy models/logs only; original and prepared data remain untouched on source. Full data migration is excluded.
- [ ] Rebuild project-private environment with working torch/CUDA and strict checkpoint loading.
- [ ] Update future training destination and telemetry while preserving source inference unless explicitly migrated and validated.
- [ ] Validate target job launch/preflight, logs, model/data paths, monitoring and regression.
- [ ] Remove temporary credentials and deliver measured migration evidence. Retain source originals until complete acceptance.

Ruling: Keep live hub/inference at source, add a separately pinned training endpoint at target — user requires future training on A100-2 while original datasets remain on A100-1; no source service interruption or unsupported data access is implied.
