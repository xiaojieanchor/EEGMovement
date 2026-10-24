# REVE capacity experiment implementation plan

**Superseded before weight download or runtime installation:** user requires the JEPA training family. REVE uses an MAE objective and is excluded from the primary route. Preserve this document as historical design only. Continue with `2026-10-16-jepa-capacity-experiment.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download pinned author REVE weights, measure actual seven-channel A100 latency, and execute an honest capacity comparison using real EEG.

**Architecture:** Add an isolated REVE adapter and experimental runner, preserving existing model/preprocessing contracts. Download server first with local fallback, verify LFS SHA-256, load reviewed local source strictly, use actual electrode names with nominal author position coordinates. Benchmark batch one before frozen probes and bounded public last-layer adaptation.

**Tech Stack:** Existing pinned SSH/PyTorch 2.6 environment; author HF source, safetensors, Transformers 4.56.2, NumPy/SciPy/sklearn/MNE.

**Spec:** `configs/training_scaling_plan.json` and `docs/模型参数规模与延迟评估_20261016.md`, approved by user's “继续”. Prior authorization delegates design details without staged confirmation.

## Global Constraints

- No LIVE promotion, held-out test model selection, raw-data mutation or interruption of unrelated GPU jobs.
- Keep the 500 ms operation interval requirement distinct from evidence-window duration and measured complete-chain delay.
- Preserve personal strict-six and conditional-seven masks; raw reference and coordinates remain unmeasured.
- Public labels remain native; pretraining exposure unknown must be stated.
- Pin revisions and hashes; do not execute unreviewed `trust_remote_code` downloads.
- No Git repository exists here; keep a file ledger instead of Git/worktree bookkeeping.

## Review Focus

- Unknown/missing electrode names must fail rather than silently shorten the position list.
- Invalid or partial weights must fail independent hash validation.
- Input windows must be fully consumed without hidden trailing samples or fabricated padding.
- Probe normalization and supervised training must never fit on validation/test EEG.
- Batch throughput, synthetic timing, and GPU-only delay must not be reported as actual VR end-to-end latency.

### Task 1: Pinned resources and runtime

- [ ] Save author revisions, source/config/license hashes and reviewed preprocessing reference.
- [ ] Test corrupt-weight, unknown-channel and input-size failures.
- [ ] Download base and, if warranted, large weights on server first; retain automatic progress and fallback.
- [ ] Install only necessary pinned dependencies in the project environment and record versions.

### Task 2: Adapter and real timing

- [ ] Implement reviewed local REVE loading, exact parameter count and spatial-preserving temporal pooling.
- [ ] Verify strict six/seven channel real forward, single-window numerical behavior and missing-channel failure.
- [ ] Measure synchronized batch-one p50/p95/p99 with real data, head and transfers; separately probe PC/SSH transport, never claim VR inference integration.

### Task 3: Controlled comparison

- [ ] Prepare shared causal 200 Hz microvolt inputs with explicit tail handling and complete dependency/source identity.
- [ ] Execute frozen personal probes on the original session split without full fine-tuning 15 trials.
- [ ] Execute subject-separated public native-task probes and bounded last-layer updates, retaining epoch zero if not improved.
- [ ] Publish immutable reports, checkpoint hashes, central progress and scientific limits; perform one independent final code review.
