# Fix the sampler's duplicate draws as v3

Type: task
Status: open

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
