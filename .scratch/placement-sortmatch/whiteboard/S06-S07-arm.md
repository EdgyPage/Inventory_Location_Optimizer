# S06-S07 -- `rank_sortmatch` as an arm, and what the simulator says (registered 2026-09-24)

## S06 -- the wiring

`rank_sortmatch` (`Optimization/config/strategies.py`, appended last):
`_RankedAssignPool` with two new hooks from `build_sortmatch_pool_fn`
(`Warehouse/placement/Assignment_Functions.py`):

* bin key `D_b + hbar M_b` in place of D (`bin_key`), hbar the catalogue's
  demand-weighted line cost per regime (`sortmatch_hbar`);
* pack key `visits_u (Dbar + h_u Mbar)` in place of the pick-effort priority
  (`order_key`), visits = max(1, Q / E[q]).

The per-aisle deques sorted by the key plus one heap over aisle heads ARE the sorted bin
list, walked without a scan.  No gain adapter (the arm cannot be priced by the unload
evaluator yet -- it would need a merge rung with these keys; S01's replay would then
apply unchanged).  Stragglers use `tmin`'s per-unit rule.

Pinned by `Tests/unit/test_sortmatch_pool.py` (37 after the revisions below): takes walk the candidates in
ascending key, every bin once; `order` is the documented key; with one SKU law the
pairing equals the scipy optimum on random instances; hbar's formula; the hook inert
without a key; a frozen tier refuses a custom key.  The rule-enumerating tests take the
18th rule (count 17 -> 18, POOLED gains `sortmatch`, an `Objective` entry).

Gate: `_toy_priced` (which carries no sortmatch arm) IDENTICAL between the pre-change
commit and the change; and a `_sortmatch_probe` toy runs end to end.  **Both met**
(`comparison_whatif_20260924_135948` vs `_140138`: IDENTICAL, 40 arms;
`comparison_20260924_140450` ran all four rule pairs, both channels, and its analysis).

### Revision after review: the pairing is decided for the whole group

The code review found what the lab had assumed away: the store PALLET queue is
`k_cap = 1` (`put_queue.store_and_fulfillment` -- no floor to re-sort pallets on), so the
drain serves pallets in ARRIVAL order and a greedy pool hands each the cheapest bin left.
The arm would then be the lab's velocity-blind `cheapest` for store pallets -- and that is
exactly the real-life constraint the user named (puts run FIFO +- a few).

The index match does not need the order; it needs the GROUP.  So:

* `_SortMatchPool(_RankedAssignPool)` gains `prepare(units)`: packs by key onto bins in
  the heap's own global order (key, aisle rank, position), index for index, fixed
  before anyone is served; `take(unit)` returns the unit its planned bin, whatever order
  the drain serves in.  Assignment uses the lookahead the dock physically has (the packs
  waiting in the group); execution stays FIFO.
* `IM._stock_ranked` calls `pool.prepare(units)` when the pool has one -- a `getattr`,
  so every other family (none has the attribute, pinned) is untouched.
* Pinned: under the full order the prepared pairing IS the greedy one, unit for unit
  (8 scenes); under arrival-order service it keeps the index match (8 scenes); an
  unprepared pool served FIFO pairs differently on >= 6 of 8 (non-vacuity).

Other review fixes: `hbar` refuses rather than defaulting when the context carried no
catalogue (diagnostics that build a context without `orders` cannot run this arm); the
line cost goes through `cost_model.per_pick` (THE formula) instead of an inline copy;
(D, M) computed once per bin per open; the grid comment corrected -- adding a rule DOES
shift every arm's plot hue (`_hsv_hex(i, N)`), which is cosmetic (colour is in
`sim_meta.json` and figures, never a database table).  Also recorded: every default
full-suite run now carries two more arms per channel (`CHANNEL_RESTOCKS` is None).

The first S07 launch ran on the pre-`prepare` code and was stopped; S07 below is the
revised arm, with the `_toy_priced` identity re-proved on it (the drain hook is shared
code).

## S07 -- prediction (committed before any simulation of the arm)

`_sortmatch_probe` (new research spec): rule pairs (sortmatch, sortmatch), the phase-1
winner (rank_cartlabor store, rank_minlabor fulfillment), (tmin, tmin) and the fifo rider;
both stock modes; fifo unloading; the 40k perf catalogue at k = 1 and k = 10 (store
demand 0.00245335 k, fulfillment 0.0181380 k), 40 batches, c = 0.95.  Pick labour =
`batch_stats.task_makespan` summed over the window, paired moving-block 95% CI
(`.scratch/aisle-churn/assets/s11_measure.py`-style).

| # | quantity | predicted | falsified by |
|---|---|---|---|
| P1 | store pick labour, sortmatch vs rank_cartlabor, uni arms, k10 | **sortmatch lower by >= 1%**, CI excludes 0 (the lab's -3.4 points of separable work, diluted by routing) | a CI on or above 0 |
| P2 | same at k1 | lower, by less (reach is ~8% of picks at declared demand, aisle-churn S08) | sortmatch higher with a CI excluding 0 |
| P3 | fulfillment pick labour, sortmatch vs rank_minlabor, k10 | within +-2% (the lab's -10 points of separable work against rank_minlabor's co-location, which the lab cannot price) | outside +-2% either way |
| P4 | sortmatch vs tmin, both channels, k10 | sortmatch lower (same machinery, height-aware bins and lifetime-work packs) | tmin lower with a CI excluding 0 |
| P5 | busiest-aisle day-cut carry at k10 | no worse than rank_cartlabor (lab S05: sortmatch lowers the busiest aisle's lines) | more carry |
| P6 | put-away placement compute (runtime_metrics per arm) | sortmatch <= rank_cartlabor's | more |

## S07 -- measured (2026-09-24)

Roots: k = 1 `comparison_20260924_141536`, k = 10 `comparison_20260924_141943` (both the
revised arm, snapshot of c6e3ec54 + the change; `assets/s07_measure.py`, results in
`assets/results/s07_k1.json`, `s07_k10.json`).  Pick labour = summed `task_makespan`,
batches 0-39; "sortmatch cheaper by" with the paired moving-block 95% interval.

### Performance -- pick labour

| channel / mode | against | k = 1 | k = 10 |
|---|---|---|---|
| store / uni | rank_cartlabor | **0.28%** [+0.09, +0.45] * | **0.78%** [+0.45, +1.05] * |
| store / opt | rank_cartlabor | 0.36% [-0.32, +0.91] | **0.98%** [+0.42, +1.59] * |
| store / uni | tmin | 0.66% * | **2.15%** * |
| store / opt | tmin | 2.11% * | **3.17%** * |
| store / uni | fifo | 1.40% * | 2.77% * |
| store / opt | fifo | 5.81% * | 4.55% * |
| fulfillment / uni | rank_minlabor | -0.07% [-0.16, +0.05] | -0.00% [-0.14, +0.19] |
| fulfillment / opt | rank_minlabor | **-1.92%** [-2.25, -1.46] * | -0.11% [-0.32, +0.06] |
| fulfillment / both | tmin | ~0 | ~0 |

Day-cut carry labour at k = 10 (store): sortmatch 15,599 / 16,185 against rank_cartlabor
15,447 / 16,149 (uni / opt); fulfillment 169,188 / 170,736 against rank_minlabor
170,408 / 166,872.

### Speed -- the put-away drain (`runtime_metrics.reord_s`, per coupled unit)

| | k = 1 uni | k = 1 opt | k = 10 uni | k = 10 opt |
|---|---|---|---|---|
| fifo (floor: no ranking at all) | 8.9 s | 9.1 s | 49.0 s | 50.1 s |
| winner pair (rank_cartlabor / rank_minlabor) | 21.2 s | 15.9 s | 133.9 s | 123.5 s |
| tmin | 14.0 s | 14.1 s | 72.9 s | 70.8 s |
| **sortmatch** | **12.7 s** | **12.7 s** | **64.5 s** | **62.1 s** |
| sortmatch vs winner, whole drain | 1.67x | 1.25x | **2.08x** | **1.99x** |
| sortmatch vs winner, ranking work above the fifo floor | 3.3x | 1.9x | **5.5x** | **6.1x** |

Whole-arm wall (`total_s`, store row) at k = 10: 200 s against 262 s (uni, -24%) and 195 s
against 265 s (opt, -27%); at k = 1 -12% and -8%.

### Verdicts against the registration

| # | verdict |
|---|---|
| P1 store k10 >= 1% cheaper than rank_cartlabor, CI excluding 0 | **direction confirmed, magnitude at the line**: 0.78% (uni) and 0.98% (opt), both CIs exclude 0 |
| P2 store k1 lower, by less | **confirmed**: 0.28% (uni, significant), 0.36% (opt, not) |
| P3 fulfillment within +-2% of rank_minlabor at k10 | **confirmed**: -0.00% / -0.11%, neither significant (at k1 the opt arm loses 1.9%, inside the band) |
| P4 below tmin on both channels | **store confirmed** (2.2-3.2% at k10); fulfillment a tie |
| P5 carry no worse than rank_cartlabor | **marginal miss** on store uni (+1.0%; +0.2% opt); not tested with an interval |
| P6 placement compute <= rank_cartlabor's | **confirmed, by 2x on the drain and 5.5-6x on the ranking work at k10** |

### Residual -- the lab against the simulator

The lab's separable objective gave sortmatch -3.4 points of realised store work over the
recorded rank_cartlabor; the simulator gives 0.8-1.0%.  About 70% of the lab's lead is
what routing takes back: a picker walks an aisle once per task, so moving one line's bin
nearer the front saves less than its standalone D.  In fulfillment the lab's ~10 points
vanish entirely: rank_minlabor's co-location (the Palm-probability term, aisle-churn S07),
which the separable objective cannot credit, is worth exactly what sortmatch's travel gain
is -- a tie, at half the put-away compute.  The ranking of RULES survives the simulator
(sortmatch > rank_cartlabor > tmin > fifo on the store); the lab overstates magnitudes.

### Revision -- the open made cheap (the feedback loop on speed)

A campaign-shaped microbenchmark (`assets/s07_pool_bench.py`: 1,400 aisles, 22,748
candidate bins, a 12-unit group, the production path -- open over the candidate list,
seat the group) showed the first sortmatch pool NO faster per group than the balance
pools: 38 ms against 41 / 39 ms (rank_cartlabor / rank_minlabor), tmin 9 ms.  cProfile
named the waste: a height multiplier recomputed for every candidate at every open, and a
full sort of the tier in `prepare` to plan a dozen units.  Two changes, neither moving a
decision:

1. `prepare` POPS the heap's first n bins (the same pop-advance-repush `take` does) instead
   of sorting the tier: O(n log A) for n units, not O(m log m) for m bins.
2. a per-bin geometry memo and key map shared across opens (a bin's (D, M) and key are
   fixed for the run -- the travel pool's `geo_memo` argument).

Per group: **38.2 -> 17.8 ms**, now 2.2-2.5x faster than both balance pools.  Proven a pure
speedup at run level: the k = 10 point rerun on the optimised code
(`comparison_20260924_143043`) is **IDENTICAL** to the first (`_141943`) on all 16 arms.

Sim-level speed on the optimised code, k = 10 (`results/s07_k10_opt.json`):

| | uni | opt |
|---|---|---|
| put-away drain, sortmatch / winner pair | 57.1 / 131.8 s store row | 54.6 / 118.9 s |
| drain speedup | **2.3x** | **2.2x** |
| ranking work above the fifo floor | 8.0 / 82.7 s -> **10x** | 4.6 / 68.8 s -> **15x** |
| whole-arm wall (store row) | 194 / 263 s (**-26%**) | 188 / 261 s (**-28%**) |

Sortmatch now places for 5-10 s more than FIFO's no-ranking floor over a 40-batch
10x-demand run, where the winner pair spends 70-83 s.

Unit + integration tiers on the change: 3,927 passed, 2 skipped.
