---
name: pool-candidate-slice-was-built-not-landed
description: "REFUTED at campaign scale 2026-09-14: the slice caps at 28.8% of the drain; its follow-on target (take()'s aisle scan) was itself RETRACTED the same day — the heap landed and the drain has no dominant term"
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

**The follow-on target named here — `take`'s aisle scan at "71.2% / ~1.09B iterations" — is
RETRACTED 2026-09-14 by measurement.** The heap was built and landed (commit bd29d2eb,
`Warehouse/placement/Assignment_Functions.py`, carrying the dict insertion rank as a tie-break
key alongside score — see [[calltree-framework-first-findings]] for why that second key is
required). Removing the scan entirely moved the drain only **6.3%**, not 71.2%. Two errors
produced the original number: (1) it used BUCKETS (224/open) as the scan width where `take`
actually iterates AISLES (measured 159/open at 400k SKUs); (2) worse and more transferable, the
per-iteration cost (0.630µs) was obtained by DIVIDING the very total it claimed to explain
(685s / 1.087e9 iterations) — a division always closes to three digits wherever the time actually
goes, it is not an attribution. The corrected numbers are independent of the total:
4,852,858 takes × 159 aisles = 771.6M iterations, measured saving from removing them = 61.5s, i.e.
**0.080µs/iteration** (two dict reads and a compare) — 0.630µs was 8x too high.

**Corrected attribution: the drain has no dominant term at 400,000 SKUs coupled.** Pool
construction 277.6s (30.8%); the aisle scan 61.5s (6.8%, now removed); everything else 562.2s
(62.4%, unattributed — needs a tracer this tier cannot afford at ~40x wall).

**Standing next candidate, deliberately not attempted so it stays attributable:** the SKU-run
boundary rebuild computes 8,904 aisle scores per open to serve 194.8 placements — 45x more scores
computed than placements made.

**Why this is a memory and not just a closed ticket:** it is the rejected-alternative pattern —
a real, measured, byte-identical-at-small-scale candidate that still isn't the right fix, with the
measurement (now at two scales) that killed it, so nobody re-builds it expecting a different scale
to save it.

**The transferable lesson:** every number in the original ticket holds at 600-6,000 SKUs; the
failure was that all three load-bearing premises were scale-dependent and the ticket said so
nowhere, so it read as a standing conclusion instead of a measurement at one point. See
[[growth-ladder-saturates-silently]] for the pattern this belongs to.

Full record: `docs/design/INBOUND_PERF_FINDINGS.md` §3; `.scratch/inbound-performance/issues/
10-the-candidate-slice-was-built-and-not-landed.md`,
`14-the-slice-is-refuted-the-target-is-takes-aisle-scan.md` (partly retracted),
`15-the-take-heap-lands-and-the-drain-has-no-dominant-term.md`. Commit bd29d2eb (the heap).
Related: [[pool-tier-loop-cost-class-before-count]], [[gpu-broker-dormant-not-for-placement]]
(same pattern: built, measured, correctly not landed), [[inbound-pool-adapter-multiplier-is-not-13x]].
