---
name: pool-candidate-slice-was-built-not-landed
description: "slicing _make_pool's candidates to k-per-bucket was byte-identical and 18.8x fewer candidates, but reverted: it regresses the MIDDLE of the catalogue range, not the ends"
metadata: 
  node_type: memory
  type: project
  originSessionId: 07c0329c-5d0a-4525-8fb1-528b0979b765
  modified: 2026-09-14T14:22:04.175Z
---

`_make_pool` (`Warehouse/placement/Assignment_Functions.py`) opens over 1,094.8 candidate bins to
place a median of **3** units per BinKey group. Slicing to k-candidates-per-bucket was built,
proven **byte-identical** on all four pool adapters (digests match on counters, every occupied
bin, and the live aisle dicts), and cut candidates 8,130,323 → 432,177 (18.8x) — then **reverted**,
not landed, because it is a regression in the middle of the catalogue range and no gate value
removes the regression without trading one scale against another:

| | 600 SKUs | 2,000 SKUs | 6,000 SKUs |
|---|---|---|---|
| unguarded | +128.3% | +20.5% | -48.1% |
| gate@4 opens | -1.0% | +32.2% | -42.7% |
| gate@32 opens | — | +14.2% | -17.2% |

Raising the amortization gate improved 2,000 SKUs by almost exactly what it cost 6,000 — there is
no single gate value that wins everywhere.

**The transferable mechanism, for whoever tries this again:** the win is DOWNSTREAM (the pool's
`__init__` handles 58 candidates instead of 1,370) and the price is LOCAL (a C-level comprehension
replaced by a Python selection loop). The trade is pure amortization, and the separating variable
is **opens per tier** — not tier size or oversize ratio, which barely move between the last two
rungs while the sign of the result flips. A future attempt should drop the memo-and-bucket into
ONE pass per open, which needs the pool to accept pre-bucketed input — a signature change in
`Assignment_Functions` that touches the restock path this effort kept out of scope.

**Why this is a memory and not just a closed ticket:** it is the rejected-alternative pattern —
a real, measured, byte-identical candidate that still isn't the right fix, with the measurement
that killed it, so nobody re-builds it expecting a different scale to save it.

Full record: `docs/design/INBOUND_PERF_FINDINGS.md` §3, `.scratch/inbound-performance/issues/
10-the-candidate-slice-was-built-and-not-landed.md`. Related:
[[pool-tier-loop-cost-class-before-count]], [[gpu-broker-dormant-not-for-placement]] (same
pattern: built, measured, correctly not landed).

**Revisit at campaign scale (2026-09-14).** The rejection above rests on a range topping out at
6,000 SKUs / 5,878 opens; the campaign-scale ladder in [[inbound-pool-adapter-multiplier-is-not-13x]]
runs 24,912 opens at 400,000 SKUs — 4.2x past that — and the measured regression band (600-2,000
SKUs, +128.3%/+20.5%) is far below anything the campaign actually runs. The rejection should be
re-tested at campaign scale before being treated as final. The no-tuned-constant version (bucket
and select in one pass per open, no memo) still needs the pre-bucketed-input signature change to
`Assignment_Functions` touching the restock path — a scope decision for the owner, not a technical
one.
