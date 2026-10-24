# JEPA capacity experiment implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Expand the actual Signal-JEPA route while retaining latent prediction rather than reconstruction, and measure whether increased capacity fits the real-time compute budget.

**Architecture:** Strictly load the existing official checkpoint, then widen only contextual encoder FFN hidden units by factors 8 and 32 with function-preserving parameter inheritance. Keep the embedding, spatial table, patch tokenizer and downstream task contract. Extra capacity is experimental and must subsequently learn via masked latent targets, EMA/stop-gradient and appropriate public-data pretraining; initial expansion alone provides no accuracy improvement.

**Tech Stack:** Existing pinned PyTorch/Braindecode environment; NumPy inheritance checks, pinned SSH, real accepted EEG.

**Spec:** User's original class-JEPA requirement and explicit correction on 2026-10-16; configs/training_scaling_plan.json.

## Global Constraints

- No REVE/MAE substitution, no LIVE promotion, no claimed official large pretrained checkpoint.
- Preserve original frozen probes, source/raw hashes, seven-channel quality policy and locked test.
- The final training objective must predict masked teacher latent representations; teacher receives no gradients and updates through EMA.
- Parameter inheritance must preserve initial evaluated outputs within measured numerical tolerance, and break duplicate hidden-unit symmetry without changing their summed output.
- Single-GPU p95 is only a compute measurement, separate from 500 ms operation interval and evidence duration.
- Workspace is not Git; preserve ledger and immutable reports rather than inventing Git commits.

## Review Focus

- Expanded widths must actually contribute to inference, not only unused predictor/teacher parameters.
- Enlarged copied weights must not be called newly trained or more accurate before real SSL.
- Masked EEG must not leak into context through tokenization, normalization or predictor input.
- EMA updates must preserve target stop-gradient; latent collapse and position-only shortcuts require diagnostics.
- Only real training-partition samples may enter pretraining; validation/test remain separate.

### Task 1: Inherited expansion and compute feasibility

- [x] Test forward-equivalent widening, symmetry breaking and invalid parameter shape failures.
- [x] Implement isolated Signal-JEPA FFN expansion without changing existing baselines.
- [x] Run actual strict-load, parameter count, inherited-output comparison, real EEG synchronized batch-one latency and memory checks on A100.

### Task 2: Correct JEPA training preparation

- [x] Preserve explicit masked-context, latent predictor, stop-gradient EMA teacher, downstream active/direction head contracts.
- [x] Document verified work and remaining SSL/data/masking acceptance; do not silently start unreviewed long pretraining.
- [x] Publish corrected plan and real feasibility evidence; independently review the added expansion code.
