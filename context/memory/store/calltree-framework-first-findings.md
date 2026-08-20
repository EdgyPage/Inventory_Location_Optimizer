---
name: calltree-framework-first-findings
description: The Tests/calltree measurement framework exists; brokers are NOT on the hot path; first convicted suspects with exponents
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-20T04:30:45.577Z
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
- Still open (Phase 7 triggers): t_sample N² (_lift_weighted_sample); t_task true-scale
  k=2.42 (chip task_78ce33c8) — unresolved by this campaign, carried forward from the
  post-fix deep ladder receipt above.

**Why:** refactors must cite a capture/ladder, not intuition; traced seconds are never
regression baselines (only untraced section walls are); an aggregate wall can hide a targeted
arm — always slice runtime_metrics per-arm before claiming or denying a win; and a fix's
mechanism hypothesis is only proven by the POST-fix exponent, never by the fix landing.

**How to apply:** hot-path renames must update SECTION_MAP (calltree_tracer.py) + its home
table in test_calltree_anchors.py in the same change — the anchors gate names the entry.
The gate already caught real rot: bench_sections._SEC_RE required 'inv=' after the log line
renamed it 'cons=' (parsed zero rows from every current run.log); fixed to accept both.
Related: [[build-inventory-tests-no-reorders]] (the dead-placement trap the scenarios fix).
