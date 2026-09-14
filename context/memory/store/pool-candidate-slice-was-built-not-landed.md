---
name: pool-candidate-slice-was-built-not-landed
description: "REFUTED at campaign scale 2026-09-14: the slice was byte-identical and 18.8x fewer candidates at 600-6,000 SKUs, but at 400,000 SKUs it is only 1.5x, would have been byte-DIFFERENT, and caps at 28.8% of the drain — the real target is take()'s aisle scan"
metadata: 
  node_type: memory
  type: project
  originSessionId: 07c0329c-5d0a-4525-8fb1-528b0979b765
  modified: 2026-09-14T14:22:04.175Z
---

`_make_pool` (`Warehouse/placement/Assignment_Functions.py`) opens over 1,094.8 candidate bins to
place a median of **3** units per BinKey group. Slicing to k-candidates-per-bucket was built,
proven **byte-identical** on all four pool adapters at 600-6,000 SKUs, and cut candidates
8,130,323 → 432,177 (18.8x) — then **reverted**, not landed, because it is a regression in the
middle of that range and no gate value removes it without trading one scale against another:

| | 600 SKUs | 2,000 SKUs | 6,000 SKUs |
|---|---|---|---|
| unguarded | +128.3% | +20.5% | -48.1% |
| gate@4 opens | -1.0% | +32.2% | -42.7% |
| gate@32 opens | — | +14.2% | -17.2% |

**REFUTED at campaign scale (2026-09-14).** The 2026-09-14 flag below asked for a re-test at
400,000 SKUs (24,912 opens, 4.2x past the range above). That re-test ran and killed the slice
outright — not just the mid-range regression, three of the premises above are scale-dependent and
none of them held:

1. **`_make_pool.__init__` is 28.8% of the receive drain at 400k SKUs, not 59%.** Timed inside
   `_make_pool`: 57.2% at 3,000 SKUs (reproduces the original 59% — the check that the timer
   measures the same thing), 31.3% at 100,000, 28.8% at 400,000. Construction shrinks as a share
   because what it competes with grows faster. This alone caps the slice at 28.8% of the drain.
2. **The pool seats ~195 units per open at 400k, not 3.** Observed takes per open: 21.85 at 3k,
   94.71 at 100k, 194.80 at 400k. The original "median k=3" was the median GROUP size, a different
   quantity. This is the serious one: the slice's byte-identity argument is "a pool asked to seat
   k units performs at most k pops total, so the (k+1)-th cheapest entry of any bucket is
   unreachable." At k=195, entries 4..195 ARE reachable — a slice keeping 3 per bucket would have
   been byte-**different**, not merely suboptimal.
3. **It reduces candidates 1.5x at campaign scale, not 18.8x.** The slice keeps min(k,
   bucket_size) per bucket; both k and bucket size grow with the catalogue, toward each other.
   Measured: 9.4x at 3k, 2.3x at 100k, 1.5x at 400k. At 400k a bucket holds 61 candidates while the
   pool pops 195, so min(k,size)=size almost everywhere.

Compounded ceiling: 92s of a 962s drain — RUN 3.24x → 3.05x. Not worth a build that would have to
reproduce three load-bearing orderings that only hold below ~6,000 SKUs. **Do not re-attempt this
slice; there is no scale left to test it at.**

**The real target, found by the same instrumentation:** 71.2% of the drain is
`_TravelBalancedPool.take` (`Warehouse/placement/Assignment_Functions.py`) scanning EVERY aisle on
EVERY placement (`for aid in by_aisle:  # original order => original tie-breaks`), plus a full
O(aisles × mults) rebuild of both caches at every SKU-run boundary. At 400,000 SKUs: takes
4,852,858 (194.80/open × 24,912 opens) × 224 buckets/open = 1,087,040,102 inner iterations; drain
minus init = 685s, i.e. 0.630µs/iteration (two dict reads and a compare, in Python). The slice
cannot touch this: it trims bins WITHIN a bucket and keeps every bucket, so the scan width is
exactly unchanged. `take` solves a SELECTION problem by SCANNING; the structural fix is a
score-keyed heap (O(n) heapify at the run boundary, lazy-deleted push for the single winner) — the
same shape that took `_admit_held` from k 1.84 to 0.94 ([[admit-held-was-quadratic]]). `take`'s
aisle iteration order is load-bearing for tie-breaks in three documented places, so this is a
SCOPE DECISION for the owner, not something to land quietly.

**Why this is a memory and not just a closed ticket:** it is the rejected-alternative pattern —
a real, measured, byte-identical-at-small-scale candidate that still isn't the right fix, with the
measurement (now at two scales) that killed it, so nobody re-builds it expecting a different scale
to save it.

**The transferable lesson:** every number in the original ticket holds at 600-6,000 SKUs; the
failure was that all three load-bearing premises were scale-dependent and the ticket said so
nowhere, so it read as a standing conclusion instead of a measurement at one point. See
[[growth-ladder-saturates-silently]] for the pattern this belongs to.

Full record: `docs/design/INBOUND_PERF_FINDINGS.md` §3 and "What this reopened, and then closed" /
"The 71.2% is one billion iterations of a linear scan"; `.scratch/inbound-performance/issues/
10-the-candidate-slice-was-built-and-not-landed.md`,
`14-the-slice-is-refuted-the-target-is-takes-aisle-scan.md`. Related:
[[pool-tier-loop-cost-class-before-count]], [[gpu-broker-dormant-not-for-placement]] (same
pattern: built, measured, correctly not landed), [[inbound-pool-adapter-multiplier-is-not-13x]].
