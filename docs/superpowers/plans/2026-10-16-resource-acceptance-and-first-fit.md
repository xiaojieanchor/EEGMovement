# Resource acceptance and first EEG fitting implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Independently accept the downloaded library and start reproducible personal and public-task EEG fitting with real data.

**Architecture:** Recompute publisher checksums and SHA-256 on the server, preserving immutable raw data. Fit the existing four frozen personal probes on their original session split. Separately prepare a subject-separated PhysioNet imagined-fist benchmark and fit native public-task heads/last encoder layer; do not turn public hand tasks into personal navigation labels.

**Tech Stack:** Existing pinned Python training environment, PyTorch/Braindecode, MNE, NumPy/SciPy, scikit-learn, pinned SSH.

**Spec:** User-authorized resource acceptance and transition to training; prior approved two-stage validation strategy and existing `training/README.md` contracts.

## Global constraints

- No raw-data changes, no test-set tuning, no LIVE promotion, and no interruption of unrelated GPU jobs.
- Personal CH3 exception remains conditional and separately compared with strict six channels.
- Keep native public task labels, subject/session identities, units and reference evidence.
- Use actual checkpoint/parameter sizes; do not equate storage size with accuracy.
- Small personal pilot remains exploratory; independent test/idle/artifact/reference gates stay explicit.

## Review focus

- Truncated or modified downloads must fail acceptance despite completed download counters.
- Training/validation/test subjects must not overlap; personal sessions must remain separate.
- Imagined hands and inter-trial rest must not become VR movement directions or proven spontaneous idle.
- Public channel names/units and decoder windows must be explicit; no fabricated physical montage certainty.
- GPU limits, run state, failures and output hashes must survive SSH disconnects.

### Task 1: Independent resource acceptance

- [x] Validate all 3,811 canonical paths against the frozen plan and provenance; recompute publisher checksum and SHA-256 in a bounded server background job.
- [x] Verify both checkpoints and personal manifests/source hashes; record GPU availability and actual model parameter counts.
- [x] Write local/server acceptance reports and durable status, preserving any failures.

### Task 2: Initial personal fitting

- [x] Run the existing pilot preflight on Signal-JEPA/CBraMod, strict-six/conditional-seven profiles.
- [x] Fit the four actual frozen-encoder head baselines; retain validation predictions and confusion matrices with the tiny-sample limitation.
- [x] Record results independently of public-task accuracy and LIVE readiness.

### Task 3: First public motor-imagery adaptation

- [x] Test annotation decoding, subject split, channel/unit handling and data lineage before implementing an adapter.
- [x] Prepare PhysioNet runs 4/8/12 with native left-fist/right-fist/inter-trial-rest labels and fixed subject splits; do not read held-out test signals during fitting.
- [x] Train reviewed adapters in an isolated output directory with progress, checkpoints, validation and failures recorded; expand to Stieger only after its native structure and label semantics are verified.
- [x] Report completed work and remaining scientific/personal-data gates honestly.

## Evidence and remaining scope

All 3811 files / 401377176324 bytes passed independent publisher plus SHA-256 checks. Four personal frozen probes and two native public-task baselines/partial updates executed on GPU 0. Stable fine-tunes did not beat frozen baselines; epoch 0 retained. See docs/资源验收与首轮训练_20261016.md. Stieger native importer and larger REVE capacity evaluation remain subsequent stages, not claimed complete. New capacity authorization recorded in configs/training_scaling_plan.json.
