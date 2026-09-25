# S09 -- the campaign on today's code; S10 -- what a 400k `plan_order` still costs

## S09 -- extrapolation (`assets/s09_extrapolate.py`)

The 20260920 phase-2 campaign has 40 batches, 11 cells, 44 units and 12 workers.  Each of
its leaves is split into reord_s and the rest.  Each part is scaled by the run B / run A
ratio of the same (cell kind, arm, channel):

* Gain cells take gmyopic's ratios.  The forecast, gated and futuresight cells price
  through the same `place_load` pool path, so this is an **assumption**, not a
  measurement.
* Arms O3 does not touch (fifo, tmin) are held at 1.0.  Their measured 0.93-1.02x is the
  A/B's noise.

| | measured (20260920) | projected, ad62b9eb (O1 + O3 + O9) |
|---|---|---|
| sum of unit walls | 83.1 h | **53.6 h** (1.55x) |
| slowest unit (fsight_w5) | 7.89 h | **4.53 h** |
| sim-stage bound max(slowest, sum/12) | 7.89 h | **4.53 h** |

Per cell, the slowest unit in hours: fsight_w5 7.89 -> 4.53, gforecast 7.63 -> 4.38,
fsight_wall 7.62 -> 4.38, ggated_h025 5.65 -> 3.28, gmyopic 4.22 -> 2.46, ggated_h050
3.89 -> 2.28, gmyopic_k8 1.66 -> 1.03.  The fifo, lifo and inb_off cells take 0.4-0.55 h.
Add ~18 min of parent setup.  The campaign's actual wall was 15.9 h, but that was three
launches; the last one alone ran at 1.03x its bound.

## S10 -- the plan's residue after O3, and three exact fixes

### Question

Run B moved the yard plan only 1.8x, though a pool open got 4.6x faster.  What is the
rest of a campaign-shaped `plan_order`?

### Instrument

`assets/s10_bench_plan.py`: T = 17 trailers x 12 units; 1,400 aisles x 3 brackets x 6
bins over 3 size tiers; the aisle books are a REAL `AisleLedger` over a 20,000-SKU
catalogue dealt ~14 per aisle (so the ledger inverse and the partner folds run at the
campaign's width); either winner family's pool.  Two earlier bench shapes were wrong, and
both errors are recorded because both flattered or hid a cost:

* Plain-dict books force `_MinLaborPool._partner_deltas` onto its per-aisle fallback,
  which production never takes.  It inflated `set_delta` to 5.6 s.
* A 40-SKU catalogue held whole by every aisle makes every partner fold 1,400 wide.

The first few timings were also taken while a toy chain or the unit tier was running, so
the per-step numbers below come from call counts and profiles.  The clean before/after is
the last table.

### Findings (cProfile, one plan)

1. **rank_cartlabor's cart hinge copied every aisle's set at every boundary: 34%.**
   `sku in ass[a]` over a gain-evaluator copy-on-write view materializes the aisle's set
   into the overlay -- ~1,400 set copies per boundary, the exact blow-up the views exist
   to prevent, re-entered through a membership test.  Fix: `_CowSets.holding(keys, item)`,
   which returns the same booleans without copying.  The live-dict path (put drain,
   initial placement) keeps its read, because its defaultdict inserts keys that
   aisle_metrics writes.
2. **The head matrices were rebuilt per open: ~25%.**  `_TravelVec` / `_MinLabVec` are
   built at a pool's first take, before any head moves, so each is a pure function of the
   round's template.  Fix: `TierSlice.memo`, one build per template (commit 88ba0514
   with 1).
3. **Then 90% of a min-labour plan was the TEMPLATE itself** (`_aisle_buckets_eager`,
   ~3,800 cursors each) and the matrix read over it.  The min-labour defer side made it
   worse: nearly every candidate holds a bin uniquely, so almost every defer open is over a
   one-off `B - hole` set that gets no template at all.  The derivation counts showed
   904 of 904 defer opens building from scratch.  Fix: DERIVE.
   * A round's sets differ by a few dozen bins, so a template is derived from the nearest
     one in the store: only buckets holding a bin of `E ^ E0` are rebuilt, and every
     other cursor is reused.
   * A cursor reads its set only for its own bucket's ids, and no set is mutated while
     the store lives.
   * One-off sets derive without being stored (`slice(..., derive=store)`).
   * The matrices are patched from the parent's: rows gathered into the new aisle order,
     and only the touched aisles rebuilt.
   * Counted after the fix: travel 198 derived / 102 roots / 0 one-off eager; min-labour
     904 derived / 102 roots.

### Proof

* `Tests/unit/test_derived_template.py` (34 tests):
  * structure (aisle order, bracket order, every cursor's pop sequence from both ends,
    len) against the eager build, for stored, one-off and chained derivations;
  * travel (cart on and off) and min-labour (min and max) pools' takes, scores and
    committed state against the eager open;
  * non-vacuity: order held and moved, and the patch ran in both layouts;
  * a stale-derivation sabotage is caught.
* `test_gain_cow_protocol`: `holding` equals `__getitem__` membership, materializes
  nothing, with a non-vacuity twin.
* `test_frozen_tier`'s template test is re-pinned to count derived memos apart from the
  bucket template.  `test_gain_plan`'s prologue oracle now withholds `derive` too.
* Toy digests: 88ba0514 IDENTICAL on all 8 cells, and the derivation IDENTICAL on all 8
  (`_toy_priced` drain and asap, `_toy_merge`).
* Unit tier: 3,506 passed.

### Measurement (quiet machine, 3 plans each; `assets/results/s10_bench_plan_clean.txt`)

The same plan order comes out on all four code states.

| `plan_order`, campaign shape | before O3 (75787912) | O3 (ad62b9eb) | + COW read, per-template matrix (88ba0514) | + derived templates | total |
|---|---|---|---|---|---|
| rank_cartlabor pools | 10.31 s | 4.15 s | 2.50 s | **1.67 s** | **6.2x** |
| rank_minlabor pools | 9.85 s | 5.19 s | 4.59 s | **1.25 s** | **7.9x** |

Run B measured the priced unit at 0.56x of run A on the yard plan, with O3 alone, which
matches this bench's O3 column (0.40-0.53x) within the load and shape differences.  The
two S10 commits take the plan a further 2.5x (cart) and 4.2x (min-labour) past O3.
**Prediction for a run C at 400k** (not yet run): the gain cells' yard and dock plan
drop by 2.5-4x more against run B.  The slowest unit (gmyopic winner, 2,699 s in B, of
which ~1,930 s is the yard plan and ~250 s the dock plan) should come in near 900-1,100
s, and the 20260920 campaign's projected bound near 1.8-2.2 h instead of 4.5 h.

## Run C (400k, a623112a) -- the prediction is REFUTED

Root `comparison_whatif_20260924_224044`.  Same spec and flags as A and B, launched on an
idle machine.

| gmyopic uni winner | run B | run C | C / B |
|---|---|---|---|
| total_s | 2,699 s | 2,652 s | 0.98x |
| yard plan | 1,929 s | 1,882 s | 0.98x |
| dock plan | 226 s | 225 s | 1.00x |
| placements | 6,294 | 6,294 | 1.00x |

Every arm lands within 0.84-1.10x of run B, which is the noise band.  **The S10 fixes did
not move the 400k gain unit.**  The bench predicted 2.5-4x.

The bench cannot see the production cost.  At 400k one `place_load` costs **~0.30 s**
(1,882 s / 6,294), while the bench's `place_load` at the same code costs **~5 ms**
(1.67 s / 306).  Production spends ~60x more per virtual placement, on something the
bench does not build.  The bench's O3 column happened to agree with run B's O3 ratio, so
the bench looked calibrated; it was calibrated on the one term it shares with
production, not on the whole.  Memory `meso-ladder-cannot-size-the-pool-prologue` warned
that a small instrument prices the wrong regime, and this is a second instance.

Next: profile a production worker at 400k (a throwaway snapshot with a cProfile hook on
`_run_strategy_worker`; spec `_perf_prof_400k` = gmyopic, winner + rider, 3 batches).

## S10 part 2 -- what production actually spends (cProfile of real 400k workers)

Instrument: a THROWAWAY snapshot of a623112a whose `_run_strategy_worker` dumps a cProfile
per unit when `PERF_PROFILE_DIR` is set, and a spec `_perf_prof_400k` (gmyopic x {winner,
rider} x {uni, opt}, 3 batches, 4 workers, the reference 400k catalogue).  None of it is in
the repo.  Profiler overhead roughly doubles the walls; the shares are what matter.

**The bench was wrong about the shape of production, not about its code.**  A 400k
`place_load` places a whole trailer: ~1,000 units against the bench's 12.  In the uni
winner, 110 `place_load` calls made 108,274 pool takes.  So the per-TAKE work dominates;
the per-OPEN work the S10 commits removed was already small at 400k.  Run C's 0.98x is
exactly that.

Uni winner unit (845 s profiled, 3 batches), by cumulative time:

| term | seconds | where |
|---|---|---|
| `_build_arm` (setup) | 484 | of which the `[era]` expected-day stamp is 180: `_aisle_routing` 241 cum incl. 4.2M `np.insert` + 4.3M `np.append`, `accumulate` 69 |
| yard + dock plan (`plan_order_iter`) | 103 | `_MinLaborPool.take` 100: the partner centroid 39, `_partner_deltas` 36 |
| DB writes (`executemany`) | 53 | mostly the initial-placement rows |
| `gc.collect` | 17 | 4 calls |

The opt winner unit adds the initial placement through the same minlabor pool: 1.32M
takes, centroid 141 s, `_rekey` (full argsort per take) 54 s.

### Two exact fixes from it (uncommitted until their proofs close)

1. **`_aisle_routing` computes every column's row terms at once.**  cumprod along axis 1,
   shifts, row sums; only the picker-state recursion stays a loop, with the loop's
   expressions.  `Tests/unit/test_aisle_routing_rows.py` freezes the per-column form as
   the oracle: `==` on all three floats over 60 scenes, rows longer than numpy's pairwise
   block included.  Microbench at a 400k aisle shape (150 x 6): 2.54 -> 0.59 ms per call
   (4.3x).
2. **`_MinLaborPool.take` memoises the partner centroid per aisle for the SKU run.**  The
   only write inside a run is the take's own `add_bin` (this SKU's index, the winning
   aisle), so the entry is dropped after the commit exactly when that index is in the
   SKU's own row.  `Tests/unit/test_minlabor_centroid_memo.py` has 22 tests.  Its
   REFERENCE is the same pool with the memo cleared before every take, because
   `_ranked_minlabor_impl` is a thin driver over the pool and would compare the memo with
   itself (memory `placement-oracles-pin-agreement-not-truth`).  The first sabotage I
   wrote was also vacuous: restoring entries after the call could not keep the one the
   take itself created.  With a dict whose `pop` does nothing, 14 of 40 scenes diverge.

Unit tier 3,589 passed.  A re-profile of production with both is running.

### The gain plan's per-take terms (profiles 2 and 3)

| uni winner, 3 batches, profiled | a623112a | + routing, centroid run-memo | + drain memo | + overlay shortcut |
|---|---|---|---|---|
| plan (`plan_order_iter`) | 103 s | 103 s | 92 s | **59 s** |
| centroid | 38 s | 37 s | 25 s | 23 s |
| `_partner_deltas` | 36 s | 36 s | 35 s | **6 s** |

* **Drain memo** (`_SHARED_CACHES['_pool_memo']`, one dict per owner, handed to every
  copy-on-write view by `_Evaluator._make_pool`; never to an identity "view", whose reads
  do move).  A load is priced ~2T times a drain, so the centroid of an UNTOUCHED aisle
  per (SKU, aisle), and the live half of the partner fold per SKU, are served from it.
  Both are pure functions of live books that no virtual placement moves.
* **Overlay shortcut.**  The fold refolded every overlaid aisle at every SKU boundary.
  Now an overlaid aisle whose partners equal the live book's
  (`rowset & view == rowset & live`, two C-level intersections) takes its live value, the
  same terms in the same order.  The first version tracked the pool's own writes;
  `test_frozen_tier.py::test_the_override_actually_moves_a_choice_on_some_seed` writes a
  view directly and caught that assumption.  The structural comparison holds whoever
  writes.
* Proof: `Tests/unit/test_pool_drain_memo.py` (77 tests).
  * POOL LEVEL, emulating the evaluator: real ledger books, several virtual opens sharing
    one memo, nothing committed; memo on == off, and the shortcut == the frozen full
    refold, on 48 scenes.
  * Non-vacuity: centroid calls fall, the fold memo is used, and the shortcut both skips
    and refolds.
  * Sabotage caught: a memo read for a written aisle, and a fold without the overlay.
    The meso scenario alone could not see either.
  * RUN LEVEL: the meso digest is equal with and without the memo.
  * Toy digests IDENTICAL on 8 cells; unit tier 3,666 passed.

## Run D (400k, 44c70a43) -- setup got faster, the yard plan did not

Root `comparison_whatif_20260925_021410`; same spec and flags; idle machine.
`run_digest --cell` against run A: **k1_off_fifo IDENTICAL, k1_off_gmyopic IDENTICAL.**

| | A | B | C | D |
|---|---|---|---|---|
| gmyopic uni winner total | 4,459 | 2,699 | 2,652 | **2,539** |
| its yard plan | 3,440 | 1,928 | 1,882 | **1,901** |
| its dock plan | 370 | 226 | 225 | 225 |
| gmyopic opt winner total | 3,155 | 1,987 | 1,950 | 1,858 |
| fifo uni winner total | 746 | 643 | 628 | **497** |
| sum of all leaf totals | 25,727 | 19,458 | 19,058 | **16,900** |
| max fulfillment startup | 885 | 500 | 490 | **395** |

**What moved:** setup.  The routing fix, f2940c97, took ~100 s off every unit, so the sum
of worker time fell 11% against C and 34% against A.

**What did not move:** the yard plan, 1,882 -> 1,901 s.  The 3-batch production profile
said 103 -> 59 s.

**The profile's window was the error.**  Those 3 batches made 110 `place_load` calls; the
20-batch run makes ~6,300, most of them in the later batches, where the yard stands 15-25
deep and a plan round's exclusion sets are large.  The per-take terms the drain memo and
the overlay shortcut removed dominate SHALLOW drains, not the deep ones that carry the
wall.  A third instrument error of the same kind: bench load size (12 vs 1,000 units),
then the profile window (early vs late batches).  Next: profile a full 20-batch unit.
