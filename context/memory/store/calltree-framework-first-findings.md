---
name: calltree-framework-first-findings
description: The Tests/calltree measurement framework exists; brokers are NOT on the hot path; first convicted suspects with exponents
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-20T23:12:13.326Z
---

Tests/calltree/ (2026-08-18) is the runtime measurement framework: deterministic tracer
(exact call counts + fast_pick thread capture), two-pass captures (untraced walls = truth,
traced trees = attribution ~40x overhead), offline viewer, exact-count compare, growth
ladders (`--ladder deep --workers 18` = the ~1h rigorous session).

**Premise verdict (grep + call-site verified):** the Schema/runschema broker layers are NOT
on the sim hot path — zero calls in the per-batch loop; startup/once-per-arm/analysis only.
The per-event costs are plain-Python: regime_of getattr chains, BinRecorder closures,
Bin.location fresh-tuple-per-access, resolve_transform string hashing per pick,
order.volume() recomputed, per-batch ThreadPoolExecutor churn, unaggregated Phase-2
_notify_pick. One real broker defect, analysis-side only: Schema identity declared_id/
declared_shape uncached (3 in-memory schema rebuilds per dataset.bind; per viewer frame).

**First growth-ladder findings (meso skus 500→8k + deep 10k→80k real runs, 2026-08-18):**
- t_reord = the DOMINANT true-scale cost: >50% of batch-loop wall at 80k skus
  (0.2→2.7s/checkpoint over 8× skus) with meso count evidence `_aisle_best` k=1.66
  (11.7k → 1.14M calls). Placement is refactor target #1 by level AND trend.
- **t_task wall k=2.31 at deep scale but k=0.92 at meso** — the hidden-quadratic class the
  deep tier exists for. Mechanism hypothesis: Task.from_batch sorts each batch SKU's
  stocked-bin set; real catalogues grow BOTH batch-SKU count and per-SKU bin multiplicity
  (eq_qty scales with demand) with N; meso pins eq to 3-8 which caps factor two. Absolute
  t_task still small at 80k — the "invisible now, wall later" case.
- batches ladder: all sections k≈1.00 r²=1.00 — no per-batch state accumulation. pickers:
  flat in-process (GIL). bins knob as built is a dud: plan_warehouse sizes bins to the
  inventory, so bins_per_aisle reshapes geometry, not scale — redesign via a fill/eq knob
  before trusting a bins exponent.

**The 2026-08-18 fix round (commits 9cdb7cd..f59f38e)** — Tests/bench/run_digest.py is the
byte-identical gate (domain-table content digests; calibrated: two identical-seed runs of
unchanged code digest IDENTICAL on 136 arms). Fixes 1+2 landed byte-identical (both post-fix
reruns IDENTICAL at 40k and tiny-keyframes-on scales). HONEST scale findings: fix 2 removes
t_task's quadratic GROWTH TERM (wall at 40k was only ~0.07s/ckpt — the win begins beyond
today's sizes); fix 1's U·A·B blowup was a meso-scenario artifact (production candidate
tiers span single-digit aisles — 40k wall unchanged, structural win only). The REAL 40k
t_reord hogs are the rank_min/maxlabor arms (~91s vs rank_labor's ~25s per arm), and the
next convicted offenders are t_sample (_lift_weighted_sample, O(k·N)≈N², k=1.74) and the
affinity chain (delta_lift_idxs k=1.60) — the future round.

**Post-fix deep ladder receipt (2026-08-19): t_task STILL k=2.42 (pre-fix 2.31 — same within
fit noise).** The from_batch per-SKU sorts were NOT the growth driver; fix 2 removed constant
factors only. The real t_task quadratic survives at true scale and is invisible at meso even
with production equilibria — suspects: stock_plan spreading per-SKU units across more bin
tiers as the warehouse grows (real-catalogue effect the synthetic scenarios don't model), or
the drain iterating indexed-but-empty bins. Absolute cost still small (~0.2s/ckpt at 80k);
open conviction, corrected suspect list.

**Memory round verdicts (2026-08-19, observability columns live since 397b7e750e1d):**
- **GC is the dominant hidden cost**: ~38s pause PER ARM at 40k (SIM_GC_DETAIL run
  comparison_20260819_121157), nearly arm-independent → driven by heap SIZE not policy;
  meso showed pause-per-gen2-collection growing 14× over a 16× ladder. NOTE gc_pause_s
  spans worker LIFETIME, total_s only the batch loop — never ratio them directly.
- **Every worker carries a constant ~3.9 GiB peak RSS, FLAT across an 8× catalogue ladder
  (k≈0.01)** — something fixed-size materializes per worker at startup; unidentified, the
  single biggest memory lever. 18 workers → 74.5 GiB peak tree on 128 GiB (no paging yet;
  more workers would page). live_objects ~571k AFTER del+gc.collect (residual floor).
- Fix class ranked #1: gc.freeze() after worker setup (+ maybe gc.disable during bulk
  load), then find the 3.9 GiB constant. #2 build_pre_snapshot churn (96 MiB/batch @8k,
  top alloc site). Per-event __slots__/tuple-churn cluster synergizes with #1 (fewer
  tracked objects = cheaper gen2 walks).
- Instrumentation cost calibration: clean tiny samples span 412-428s across a day (±2%);
  never compare cross-day walls without a same-day control.

**2026-08-19 six-phase campaign landed (commits 824375c, bdcd1c3, 10ecd39, f9979c3, f30cf41,
126282c, 831571f, b91cf38, fix 1375cbb) — RESOLVES the two verdicts above; every phase
digest-IDENTICAL at tiny+40k scales (Tests/bench/run_digest.py).** Measured at 40k
SIM_GC_DETAIL, 136 arms, pre-campaign reference comparison_20260819_121157 → final
comparison_20260819_225805: batch-loop wall 31.9 → 25.8s (−19%), gc_pause 24.9 → 2.4s/arm
(−90%), peak worker RSS 3944 → ~1200 MiB (−70%, 18-worker tree ~74 → ~21 GiB), live objects
571k → 496k. **"GC is the dominant hidden cost" and "the flat 3.9 GiB peak is unidentified"
are SUPERSEDED — GC is now a 2.4s/arm residual and the RSS constant was found and fixed.**
Mechanism facts worth keeping:
- The 3.9 GiB flat worker peak WAS the affinity fetchall (streaming _load_matrix +
  gc.disable during the fill fixed it; pragmas were irrelevant, ~350M rows).
- The GC pause was load-era collections walking a heap that already held the inventory —
  NOT the batch loop; gc.freeze alone BACKFIRED (empties the long-lived denominator → full
  collections fire near-constantly; kept with a 5x gen-2 threshold companion, net neutral,
  compounds later with the other fixes).
- fused_pre_snapshot killed the 96 MiB/batch pre-snapshot churn (t_pre 1.81 → 0.32s/arm);
  build_pre_snapshot survives for test_visualization_data.
- __slots__ landed on PickEvent/PickerEventRecord/StorageUnit-family/Bin/Order (Order slot
  census: 20 names incl. subtype/stock_qty/_is_reorder; labor_cost/handle_var class defaults
  relocated to all four construction sites).
- Phase 6: SKU-run caches in _ranked_minlabor_impl and cluster_map choose-aisle. THE
  TRANSFERABLE LESSON: value-correct is not byte-identical — a cache held across a set's
  growth flipped _delta_lift_from_row's iterate-the-smaller-side summation ORDER and drifted
  one ulp, moving one placement; the fix is refreshing exactly what a commit mutates (winner
  aisle) — frozen-oracle + mutation tests in Tests/calltree/test_rank_cache_equivalence.py.
  Production win modest (cluster 48.5→45.4s/arm; meso −55%) because 40k reorder waves carry
  short same-SKU runs; cluster_map_rank arms remain the top t_reord cost and their remaining
  time is NOT choose-aisle.

**Phase 7 (2026-08-20, commit e7c9ed9) closed all three Phase-6 open items — report-and-stop,
one refactor:**
- **t_sample N²: CONFIRMED quadratic** (0.83s/batch at 40k skus, 21.6s at 160k). Fixed as a
  VERSION, not a rewrite: `BatchConfig.sampler` (Warehouse/picking/Workload_Builder.py) defaults
  `'v1'` (byte-identical to pre-fix, digest-proven no-op at tiny+40k scales); `'v2'` is a Fenwick
  sampler, ×45 faster at 160k (0.48s), fingerprinted apart in
  `Optimization/simdriver/batch_precompute.py` so v2 batches never collide with v1 caches.
  Byte-identity was impossible for ANY O(log n) sampler — v1's draws depend on the sequential-
  cumsum float grouping order, so opting into v2 starts a new results era. Test:
  `Tests/unit/test_batch_sampler_v2.py`.
- **t_task k=2.42 chip (task_78ce33c8): investigated, no refactor.** Post-campaign deep ladder
  reads k=3.28 r²=0.93 — steeper only because everything AROUND it flattened (t_reord 1.10,
  t_sim 0.99), not because t_task itself got worse. True-scale attribution (one real 80k arm,
  production bins 100k/132k, cProfile): the whole section is 0.81s/arm of distributed per-item
  bin-selection work — per-SKU stocked-bin multiplicity is the growth factor, with no single
  provable-identical restructure available. VERDICT: no fix; the trigger is catalogue growth
  toward ~300k SKUs (~8s/arm at 160k, ~77s at 320k extrapolated) — revisit only past that scale.
- **cluster_map remainder ("not choose-aisle"): attributed, no quadratic.** At 20k skus the time
  is spread across the semantically-required per-wave `_group` rebuild (~31% of place_wave —
  by_aisle must rebuild since bins fill between waves), cache-priming dictcomps, and
  choose/centroid/pick constants. Report-and-stop.
- Deep ladder archived: `growth__knob-skus_ladder-deep_seed-42__20260820T134318Z_10e360de945d`
  in `Tests/calltree/out/archive`.

**Why:** refactors must cite a capture/ladder, not intuition; traced seconds are never
regression baselines (only untraced section walls are); an aggregate wall can hide a targeted
arm — always slice runtime_metrics per-arm before claiming or denying a win; and a fix's
mechanism hypothesis is only proven by the POST-fix exponent, never by the fix landing.

**How to apply:** hot-path renames must update SECTION_MAP (calltree_tracer.py) + its home
table in test_calltree_anchors.py in the same change — the anchors gate names the entry.
The gate already caught real rot: bench_sections._SEC_RE required 'inv=' after the log line
renamed it 'cons=' (parsed zero rows from every current run.log); fixed to accept both.
Related: [[build-inventory-tests-no-reorders]] (the dead-placement trap the scenarios fix).

**Round 2 (2026-08-20, commits c8e2096, c2fcc2b, 2e7e8d0) — a fresh 40k section ranking made
round 1's ranking obsolete: t_reord ~50%, t_save ~31% (never attributed before), then
t_extract, t_sim.**
- **C1 `save_checkpoint_bundle`** (`Optimization/persistence/Picking_Data.py`): the eight
  per-checkpoint writers (`_insert_*` bodies extracted verbatim, public `save_*` callers
  unchanged) now share one connection and one commit instead of eight separate
  `_open_db`+commit+close pairs (92 opens per 15-batch arm). t_save 8.72→4.87s/arm, loop wall
  27.8→24.2s. Attribution: only ~2s of the 8.7s was CPU (cProfile on local disk) — the rest
  was drive latency × per-writer connection churn. Related finding, same investigation: the
  within-run slowdown is NOT cumulative write-wait, it's a synchronized 18-worker checkpoint
  storm early in the run (arm-identity-controlled quartile analysis; Q2 +8s dev with ALL
  sections inflated) that decays as arms desynchronize; C1 shrinks the storm rather than
  eliminating it.
- **C2 affinity sidecar** `affinity.db.arrays.npz` (`Warehouse/catalog/Affinity_Store.py`,
  written atomically via tmp+`os.replace`): holds the finished CSR arrays + sku index so every
  worker after the first stops re-decoding the 14M-row SQL affinity.db. Worker load
  29.1→0.3s/arm, RUN WALL 16.8→10.6 min (−37%). Declared in the profiles-tree contract as an
  optional derived artifact (head `7de9027f83ab`), safe to delete, regenerated on demand.
  Staleness stamp = main-db size + header change counter + mtime_ns + WAL size — TWO terms
  earned by failing tests, kept as regressions
  (`Tests/unit/test_affinity_load_equivalence.py`):
  - WAL-mode commits don't move the main file's header, so size+counter alone served a stale
    sidecar over fresh commits — WAL size added to the stamp.
  - a fixed sidecar name + weak stamp let two same-shaped seed-variant DBs in one directory
    serve each other's arrays — the sidecar name now carries the source db filename.
- **C3 t_extract: CLOSED, report-and-stop.** `lift_sum` is a digest-stored column whose float
  is locked to scipy's submatrix summation order — no byte-identical restructure exists.
  `lift_cache` hit rate measured 24.4% (75% of task sku-sets are unique). A restructure here
  is a results-era decision, same class as the sampler v2 opt-in.
- **C4 `_co_demand_ranked_impl` SKU-run cache + priority sort-key memo**
  (`Warehouse/placement/Assignment_Functions.py`): `aisle_key = (mass, ±d0)` cached per aisle
  across a SKU's run, both components refreshed winner-only post-commit (the Phase-6 ulp
  lesson applied from the start, not retrofitted). Meso `check_reorders` 6.33→2.94s; 40k comp
  31.1→26.5, expn 21.2→17.6 s/arm. Frozen oracle verbatim in
  `Tests/calltree/test_rank_cache_equivalence.py` (now 3 oracles, 6 tests).
- Batches-ladder k=2.38 on StorageUnit/`_SortedBins` re-examined and closed as a WARM-UP RAMP
  fitting artifact (production equilibria: early batches fire few reorders; count ratios
  decelerate toward ×2 = linear) — not a quadratic, do not re-flag it.
- Post-slots deep-RSS check: worker peak still flat ~1.15 GiB over an 8× SKU ladder — memory
  headroom permits roughly 5× more workers if CPU allows.
- Round-2 ledger at 40k vs round-1's close (`comparison_20260820_090918` →
  `comparison_20260820_132857`): loop wall 27.8→24.1s, save 8.72→5.26s, run wall ~17→~10.6 min.
  Cumulative vs the pre-campaign baseline (`comparison_20260819_121157`): loop wall
  31.9→24.1s (−24%), run wall roughly halved.

**2026-08-20 (commit 21f3b3c): the v2 sampler (t_sample fix above) became the default era for
all new runs — see [[v2-sampler-era]] for the baselines, the v1 escape hatch, and the
row-comparability trap.**

**Round 3 (2026-08-20, single fix commit 2cdea43) — the honest-closure round: one small
verified win, everything else CLOSED at its measured floor.**
- Wall decomposition of the v2-era reference (`comparison_20260820_151204`, 10.4 min): parent
  lead-in 1.1 min, worker phase 5.3 min, parent tail 4.0 min. The long-suspected "60s/arm
  startup" line from earlier rounds was an amortization error — real per-arm startup measures
  ~9s mean.
- **R3-C2 LANDED**: inline `analyze_run` (`Optimization/run_simulation.py`) now passes
  `granularity='graph'` whenever the analysis pool has >1 worker (was the `'config'` default —
  4 jobs on 18 workers, 3.3 min with 14 idle; same fix class as
  [[analyze-run-granularity-worker-saturation]], just wired at the inline call site). Honest
  outcome: only ~0.5 min reclaimed (tail 4.0 → 3.5 min) — post-fix attribution shows the stage
  is at its matplotlib CPU floor (savefig/tight_layout/tick layout; metric_grids 24s/job,
  scorecards 13.6s/34 figures). Further reduction means rewriting the plot suite —
  report-and-stop. Digest-only gate runs should pass `--no-analyze` instead (~3.5 min saved per
  gate run, no product change).
- Startup: CLOSED, no candidate — dominated by the initial fill (`_stock`/`_stock_per_unit` +
  `enqueue_all`, ~3.2s), which IS the strategy under test; loaders already fast (inventory
  0.33s, warehouse build 0.48s).
- t_save remainder: CLOSED — 1.47s CPU (local-disk bundle) + ~3.7s drive in ONE commit per
  checkpoint; local staging is disqualified because `--resume` and mid-run readers need the sim
  DB at its canonical path.
- In-era deep ladder (archive
  `growth__knob-skus_ladder-deep_seed-42__20260820T231003Z_2cdea4386ab7`): t_reord 1.06, t_sim
  0.92, t_save 0.79, t_extract 0.63; t_task k=3.92 in-era (v1-era read 3.28) — same standing
  verdict, absolute cost still trivial, trigger still ~300k SKUs. 80k rung wall 17.3 min (was
  24.9 pre-round-2).
- **Standing state after three rounds**: at 40k the system is at a local optimum under the
  byte-identical constraint — loop ~24s/arm, run wall ~10 min (~5.3 min worker + ~3 min
  analysis CPU floor + ~1 min lead-in). Future levers are operational (more workers — RSS
  headroom permits ~5x; `--no-analyze` for gate runs) or era-level decisions, not further
  identical-refactors.
