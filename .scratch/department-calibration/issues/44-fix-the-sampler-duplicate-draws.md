# Fix the sampler's duplicate draws as v3

Type: task
Status: resolved

Found 2026-09-12 while building
[Characterise the draw probability](40-characterise-the-draw-probability.md), which measured the
declared sampler's own output and found it does not deliver the batch it is asked for. The defect
is ESTABLISHED and isolated; this ticket builds the fix. Execution override is ON.

## Question

**The v2 batch sampler re-selects SKUs it has already drawn.** `_lift_weighted_sample_v2`
(`Warehouse/picking/Workload_Builder.py:174-213`) loops exactly `k` times and `break`s only when
every weight is zero, so `selected` always holds `k` entries -- but `Batch.items` is a **dict keyed
by sku** (`Workload_Builder.py:268`), so a repeat collapses silently and the batch delivers fewer
distinct lines than the era declared.

**Isolated to v2.** Same section, same seeds, same `k`, three batches of the reference pair's
fulfillment catalogue:

| sampler | batch 0 | batch 1 | batch 2 |
|---|---|---|---|
| **v2** (default since 2026-08-20, `21f3b3c`) | 179 dups (7.98%) | 423 (12.24%) | 73 (2.94%) |
| **v1** | **0** | **0** | **0** |

v1 recomputes `w = base * lift_mult` and its `cumsum` from scratch on every draw
(`Workload_Builder.py:103-112`); v2 maintains a Fenwick tree by incremental `set()` deltas
(`:139-158`). That is the only structural difference between them.

**The repeats land on tree boundaries**, which is the fingerprint of a traversal that ran off the
end: the most-repeated positions are 131,071 / 65,535 / 12,287 / 155,647 / 81,919 / 114,687 /
98,303 -- every one of them `(a sum of distinct powers of two) - 1`. In the draw-probability
artifact the two worst (2^17-1 and 2^16-1) carry `p_s` of **0.8044** and **0.7582** against a
section mean of 0.0167: pure numerical artifact, not demand.

**Mechanism, INFERRED and not yet proven** -- prove or replace it before fixing: `_Fenwick.total()`
sums `t[n], t[n - lowbit(n)], ...` while `find()` descends a different set of nodes, so once
`set()` has accumulated float drift the two disagree, and the slice of `r.uniform(0.0, total)`
above the reachable prefix terminates at whatever boundary the descent stops on. `find` then
clamps with `min(pos, self.n - 1)` (`:169`). A cheap decisive check: assert
`abs(fw.total() - sum(fw.w)) ` stays at zero through a draw, and count how often `find` returns an
index whose weight is already 0.

**What it costs the era.** Measured across all 40 batches of `comparison_20260912_055947`:

| section | requested `num_skus` | delivered `len(items)` | shortfall | batches short |
|---|---|---|---|---|
| fulfillment | 2,980.6 | 2,723.1 | **-8.64%** | **40/40** |
| store | 618.1 | 611.8 | -1.02% | 29/40 |

The era declares fulfillment at `n = 2,903` lines/day and the generator delivers ~2,671, so the
coverage fixed point and the derived picking crew are both sized ~8.6% high on that leaf. This is
not a sampler curiosity -- it is this map's destination ("throughput in equilibrium").

## What this ticket must produce

1. **A v3 sampler** (user decision, 2026-09-12). NOT a repair of v2 in place: v1 and v2 stay
   byte-reproducible so the archive keeps its meaning, and `batch_precompute.batch_fingerprint`
   already hashes any non-v1 sampler name, so v3 files can never be served from a v2 cache
   (`batch_precompute.py:84-88`). The v1 -> v2 flip is the precedent
   (memory `v2-sampler-era`), and it makes the comparability break explicit and opt-in rather
   than a silent change of what "v2" means.
2. **A test that fails on v2 and passes on v3**: over a section large enough to drift (the small
   fixtures in `Tests/e2e/test_batch_precompute.py` will NOT reproduce this -- the defect needs
   thousands of `set()` calls), assert `len(selected) == len(set(selected))` and that the
   delivered distinct count equals the requested `k`. The three-batch fulfillment reproduction
   above is the oracle.
3. **The era's declaration.** Decide and record whether the calibrated era flips to v3 -- it
   should, or the fix buys nothing -- and what that does to the archive. This is at least the
   sixth comparability break and probably the widest: it moves every batch sequence, so every
   absolute number in the v2 era moves with it.
4. **Whether `Batch` should refuse rather than collapse.** The dict swallowed this for weeks. A
   `len(items) != k` assertion at construction would have caught it on the first batch; decide
   whether it lands (and at what cost on the hot path).

Done when v3 delivers exactly `k` distinct SKUs on the reference sections, v1/v2 are proven
unchanged, the era's sampler declaration is recorded, and the memory `v2-sampler-era` is amended.

**Method warnings:**
- v1 costs ~21.6 s/batch at 160k SKUs against v2's ~0.2 s -- do NOT re-run a v1 control at scale
  without budgeting for it (`Workload_Builder.py:68`).
- Floats compare with a tolerance, never `==` -- except where this ticket deliberately asserts an
  exact integer count.
- The affinity partner map is ~14M entries and over a gigabyte resident per process; memory, not
  compute, sets the worker count (see 40's answer).

## Answer

Resolved 2026-09-12. **v3 is built, tested, and is the era's declared sampler.** It delivers
exactly `k` distinct SKUs on both reference sections, 40/40 batches, at 1.1-1.5x v2's cost.
The inherited mechanism was **wrong** and is replaced by a proven one.

### 1. The mechanism: NOT float drift. Catastrophic cancellation.

This ticket carried an inferred account -- `total()` and `find()` descend different nodes, drift
lets `uniform(0, total)` overshoot the reachable prefix, `find` clamps on a boundary. **Refuted,
two ways.** In batch 2, **0 of 73** duplicates had `u > true_total` (batch 0: 17/179), so
overshoot cannot be the cause. And a synthetic `_Fenwick` at the same `n` and `k`, driven only by
drift under three weight laws with and without partner updates, produces **zero** duplicates --
its error stays at 1e-12..1e-14, twelve orders of magnitude too small to matter.

The real cause is the **weight dynamic range**, which nobody had measured. Affinity lift values
are **2.8-5.0 (median 4.5)** over a **median 35 partners**, and the conditional-demand model
multiplies lift in once per already-selected partner, so `lift_mult` compounds to **7.8e20**
against base frequencies of ~1e-6: roughly **26 orders of magnitude**, against float64's 16.
A Fenwick node is maintained as `t[j] += (new - old)`. Zeroing a ~1e19 weight subtracts a ~1e19
delta from nodes that also aggregate ~1e4 of small weights; those are annihilated
(`1e19 + 1e4 == 1e19`) and **the node keeps the difference as phantom mass**. Measured through
one fulfillment batch, the Fenwick's `total()` ran **14.6% above the true sum of its own leaves**
(1.19e5 vs 1.04e5), and the heaviest live SKU legitimately held **30-89%** of live weight from
step 500 on. The draws then descend into ground that is already dead.

The `2^k - 1` index signature this ticket reported is a **consequence, not a cause** -- once
`rem` exceeds the live mass in a subtree the descent takes every remaining bit -- so it should
not be reasoned from. Reproduction, instrumented:
[repro_sampler_duplicates.py](../assets/repro_sampler_duplicates.py) (reproduces 179 / 423 / 73
exactly, and derives every path from the run's own `run_spec.json`).

**Why v1 is clean structurally, not by luck:** `np.searchsorted` over a cumsum whose inactive
entries are exactly 0.0 cannot return a zeroed index -- that would require
`prefix(i) < u <= prefix(i)`. v1 also rebuilds `w` and its cumsum from scratch every draw, so no
residue survives. v1's own annihilation is harmless: it zeroes probabilities already ~1e-22.

**v2 has a SECOND failure mode nobody had seen.** On a strongly-clustered section its total goes
non-positive and the draw loop **breaks early**, returning fewer than `k` with no repeats at all.
A test that only checks "the batch was short" cannot tell the two apart, which is why the gate
below pins a fixture that genuinely collapses.

### 2. v3: `_SegTree`, and why it cannot fail this way

`Warehouse/picking/Workload_Builder.py` gains `_SegTree` + `_lift_weighted_sample_v3`, registered
as `_SAMPLERS['v3']`. Every internal node is **recomputed from its two children**, never adjusted
by a delta, so no residue can survive a removal -- the same property v1 buys by rebuilding each
draw, at O(log n) per update instead of O(n) per draw. Measured: the root is **bit-for-bit
identical** to a fresh rebuild of its own leaves through a whole fulfillment batch (relative error
`0.000e+00`, where the Fenwick reached `1.457e-01`).

`find` branches right **only into a subtree holding positive mass**, so every step stays inside
positively-weighted ground and the returned leaf is **guaranteed live** -- a structural guarantee,
not a tolerance. It is also total: a `u` at or above the root (which `random.uniform` can return)
walks to the last live leaf instead of off the end. Its boundary rule is strict where v1's
`searchsorted` is side='left'; they differ only on exact prefix boundaries -- measure zero for a
continuous draw -- and the strict form is exactly what buys the guarantee.

**v1 and v2 are untouched.** The only line removed from `Workload_Builder.py` is the `_SAMPLERS`
dict literal; both function bodies are byte-identical, `batch_fingerprint` needed no change (it
already hashes any non-v1 sampler name), and `Tests/unit/test_batch_sampler_v2.py` still passes.

| | store | fulfillment |
|---|---|---|
| requested / batch | 618.1 | 2,980.6 |
| v2 delivered | 611.8 (**-1.02%**, 29/40 short) | 2,723.1 (**-8.64%**, 40/40 short) |
| **v3 delivered** | **618.1 (0.00%, 0/40 short)** | **2,980.6 (0.00%, 0/40 short)** |
| v3 cost vs v2 | 1.10x | 1.53x |

v2's figures reproduce the run's own `_batches_*.pkl` exactly. v3 is still ~1/35th of v1's 21.6
s/batch. The store's `std_fraction` is `mean/3`, not `mean/4` -- read it off the pickled
`BatchConfig`, it is not in `config.json`.

### 3. The era flips to v3 (user decision, 2026-09-12)

`SAMPLER = 'v3'` in `Optimization/config/settings.py`; `--sampler` accepts `v1|v2|v3`. **This is
the seventh comparability break and the widest**: it moves every batch sequence, so the coverage
fixed point, the solved line floor and the derived picking crew move with it, and the era's
CALIBRATED status (31 reading clean) is **provisional again** until
[Re-run the reference pair and record the form](43-rerun-and-record-the-form.md). Batch caches are
fingerprinted per sampler, so no v3 run can be served a v2 file.

### 4. A collapsed batch now refuses (user decision, 2026-09-12)

`Batch.__init__` raises when `len(items) != len(selected)` under any sampler that promises
distinct draws (v1, v3); **v2 is exempt on purpose** so its archive stays reproducible -- an
unconditional guard would make every v2 run refuse. The check is against `selected`, never `k`:
a short draw is legitimate when the live weight runs out, and only the collapse is the bug. It is
O(1), and it would have caught this on the very first batch.

### 5. Gates

`Tests/unit/test_batch_sampler_v3.py`, 9 tests, green -- and **proven non-vacuous**: pointing v3
at v2's function makes the two behaviour gates fail (1,413 repeats; 123 lines of 1,536) while the
`_SegTree` invariant tests correctly stay green. The fixture is a mutual-partner clique
(n=3072, clique=64, k=1536) because a uniformly-random partner graph does **not** reproduce the
defect -- the lift multiplications spread too thin. v2 fails it on 10/12 seeds, v3 on 0/12.
`test_batch_sampler_v2.py::test_unknown_sampler_raises` used `'v3'` as its *unknown* name and was
moved to `'v99'`.

### 6. A finding for the sampler effort, deliberately NOT ticketed here

The lift compounding to ~1e20, and one SKU holding 30-89% of a batch's live draw mass from step
500 on, is the **declared weight model working as written** -- v1 and v3 implement it faithfully.
Whether it *should* compound without bound is a question about the sampler's DESIGN, which this
map's Out-of-scope carve-out explicitly excludes (a defect is in scope; the design is not). It is
recorded in that entry as seed material rather than resolved here. It is also directly relevant
to [Re-measure the fill-law targets under v3](45-remeasure-the-fill-targets-under-v3.md)'s
question 2, which must now separate the defect from genuine affinity concentration.

Memories: [[v2-sampler-redraws-selected-skus]] rewritten with the proven mechanism (the refuted
one is named so it cannot come back), new [[v3-sampler-era]], [[v2-sampler-era]] marked closed.
