# Joint 32-channel navigation repair

**Goal:** Repair the demonstrated full-channel, optimization and policy-selection defects; run a source-bound A100 pilot before committing more compute.

**Spec:** User-approved navigation plan in this chat and `docs/gnautilus/有状态JEPA_训练与实时接口_20261025.md`; diagnostic evidence in this chat's `outputs/navigation-jepa-diagnosis/`.

**Architecture:** Version 2 uses causal broadband filtering on all 32 physical columns, an OpenBMI-initialized shared 32-channel spatial/GRU encoder and an EOG-initialized frontal encoder. Features meet before the joint five-class head. JEPA acts on both encoders and their joint latent. Private fitting accumulates equally weighted complete-trial losses before an optimizer update, with persistent state across truncated backpropagation chunks. Version 1 remains loadable.

**Constraints:** Preserve raw columns (16=Cz,19=C5), source hashes, whole-trial roles, UNKNOWN=-1, original public task semantics, electrical QC, manual inference/action starts, 250ms freshness and 180ms Quest tickets. Real signal processing and training only on the pinned A100. Pilot never reads test signals; it registers an offline research candidate to satisfy the existing durable training-job lifecycle, without selecting or enabling it. Fixed data have only five independent training trials and source/class confounding; no accuracy promise.

**Pilot:** Seed 17, transferred JEPA and transferred no-JEPA control, at most 300 native/SSL updates and 100 private rounds each. A locked aggregate-budget receipt subtracts prior JEPA study wall time from the approved 12 GPU hours. Training/validation scores and joint CE are logged together. No old-architecture SSL reuse.

## Task 1: Full-channel causal model
- [x] RED: Assert every physical channel reaches joint logits; future perturbations do not change earlier outputs; irregular chunks equal continuous inference; source spatial columns map by actual name.
- [x] GREEN: Implement `gnautilus_ml/joint_stream.py`, safe v2 artifact loading and conservative joint-score policy. Keep v1 unchanged.
- [x] Verify focused synthetic tests.

## Task 2: Governed optimization and calibration
- [x] RED: All-reject calibration is explicitly unsuccessful; one round updates only after all class-balanced trials; pilot cannot read test or reuse v1 SSL; protocol binds sources, recipe and remaining aggregate budget.
- [x] GREEN: Implement `joint_training.py`, v4 protocol dispatch, source-bound continuous regions, diagnostics and pilot result lifecycle. Use train-only moments and held-out exclusion.
- [x] Verify complete synthetic entrypoint and failure/resume paths.

## Task 3: Real pilot and delivery
- [x] Refresh source catalog; verify local and pinned remote synthetic checks, then deploy an immutable release while service is idle.
- [x] Lock/check exact protocol before training, including actual entrypoint hash and aggregate budget.
- [x] Run A100 pilot through real checkpoints and train/validation replay. Review actual loss, gradient and confusion evidence; preserve negative results.
- [x] Prepare report, results, protocol, hashes and remaining limitations. Hardware acceptance remains deferred.

## Review focus
Pooling remainder under irregular packets; zero-initialized added spatial columns; eye-specific temperature does not inherit joint calibration; all-reject policy cannot win; raw supports adjacent to held-out roles reset filters and hidden state.

## Progress ledger
Pre-flight: old training/runtime interfaces use `motor` for a 24-channel branch. The new version retains this internal parameter name for native teacher compatibility, but it contains all 32 channels; all user-facing evidence describes shared full-channel evidence. No existing dirty changes are discarded or committed together with unrelated work.

Task 1: complete — synthetic causal, all-channel-gradient, frequency-preservation and packet-invariance checks passed. Runtime v1 remains supported alongside v2.

Task 2: complete — complete synthetic pilot exported and registered an offline-only artifact without opening test data; class-balanced accumulated updates and explicit all-reject selection passed. Final Python suite reached 501 tests plus 42 subtests, with 36 pinned-A100 synthetic checks and nine web scripts passing. Source review found and fixed official-HDF5 episode handling and exclusion of new private candidates from the public teacher catalog, each with a failing regression first.

Task 3: pilot complete — attempts and immutable releases retained. Attempt 1 failed before optimization (official HDF5 lacks canonical descriptor); attempt 2 committed 300 SSL and 35 private rounds before stopping for weak development results; attempt 3 was cancelled after QC found 3/4800 private draws crossed a rejected electrical interval. These were not registered as qualified results. Corrected v4 job `9fea6135e1ed46799a1a8c819ca55396` completed paired seed17 / 300 native-SSL updates / 10 private rounds on release `e21c9f3c4b03614fe77e8b7379c83859fa301085db127167e0b34be6efc22b79`. Both arms' 4800 private draws had zero rejected-range, outside-approved-region and held-out-context intersections. The successful pilot took 1382.72s; the prior budget receipt remains attached.

Offline research model `c9e59515e0131e84e7c8fb60ab2973dd52642d441461291e37d944bd80e3bfe3` registered without selection or auto-start. Selected JEPA epoch1 development BA23.62%, no-JEPA epoch10 BA23.00%; both coverage0, no_useful_policy. All 141 dataset source files and 127 public weight hashes remained unchanged. No independent test, cross-wearing or hardware success is claimed.

Temporal investigation: complete — read-only pinned-A100 probes of old epoch5/60 and new epoch1/10, 1–8s and persistent state, train/validation only. Same-support encoder-history changes have little/no benefit. Diagnostic causal probability means help new last epoch10 development BA23.37→38.15%, but forward recall becomes0; new selected epoch1 decreases23.62→22.99 and old epoch60 decreases16.56→7.58. UNKNOWN head/tail outputs enter evidence memory; labels mask metrics only. No confidence early-stop gate ever triggered, so this is growing-history/long-mean sensitivity, not proven signal-adaptive stopping. No post-hoc checkpoint replacement or action deployment. Learned event-aware multiscale memory remains a proposed next experiment.

Final review: one read-only independent reviewer was dispatched under the explicitly applicable requesting-code-review / executing-plans skill requirement. Three P2 findings were reproduced and fixed: SSL draws crossing QC resets, action confirmations dependent on transport packet count/prewarm, and native teachers mutable across resume. Final regression receipts are preserved. No further agents, training runs or hardware acceptance are planned in this turn.

Latest user steering: label the promising training methods now, continue optimization after more data. Preserve full32 transfer as the common base, paired JEPA/no-JEPA as unresolved alternatives, and event-aware multiscale evidence as a priority hypothesis. Deprioritize fixed longer input alone and direct long-average control. Method flags are internal research metadata, not additional visible dataset labels. Current scope closes with archived evidence; the original three-seed/full-ablation/hardware plan remains incomplete and deferred.

Subsequent explicit authorization reopened optimization and requested ocular-classifier inference on existing real data. The follow-up is documented in `docs/gnautilus/眼动联合诊断计划_20261025.md`: full32 multiscale/frontal readouts, a confirmed weak-loss support-weighting defect and paired isolated head experiment are complete. Training-service fix is deployed with source-bound regression; selected weights, raw data, test isolation and manual starts remain preserved. It is a separate exploratory follow-up, not completion of the deferred full three-seed or hardware plan.
