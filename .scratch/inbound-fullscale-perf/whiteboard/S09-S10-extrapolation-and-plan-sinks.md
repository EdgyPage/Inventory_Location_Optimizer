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
