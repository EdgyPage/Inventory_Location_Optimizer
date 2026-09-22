# 02 - a bench that prices a pool open in seconds, and reports what a replica would weigh

Type: task
Status: resolved

`.scratch/phase-2-campaign/issues/04` in full: nothing between the 90 s toy digest and a 3 h
campaign-scale probe can price a pool open, and the meso ladder gave the wrong sign once
(memory `meso-ladder-cannot-size-the-pool-prologue`). Every attempt on the evaluator without
this costs three hours to score. The user's decision (Q6): bench first.

## What to build

`Tests/bench/bench_pool_open.py`, exactly as ticket 04 there specifies: a synthetic
`FrozenTier` at the measured campaign shape (1,400 live aisles x 3 height brackets x 6 bins =
25,200 bins) through `Assignment_Functions.freeze_tier`, reusing `test_frozen_tier.py`'s
`_wp` / `_bins` fixture idiom so the D ties that make the tie-breaks real are planted. Time:

1. `TierSlice.aisle_buckets()` alone (walk + sort vs `_Cursor` constructions; read 3.685 ms,
   33/67 on 2026-09-20);
2. open-and-seat through a real `_TravelBalancedPool` over an eager slice (13.35 ms);
3. the same over a shared template store (9.23 ms).

**Assert the emitted `(aisle, x_phys, score)` sequence is identical between (2) and (3)**, so
the benchmark is also a correctness check.

## Two things this map adds

- **A `_MinLaborPool` row.** The campaign's winner pair is `rank_cartlabor`/`rank_minlabor`;
  both families open under the evaluator. The record's numbers are all travel-balanced.
- **The replica's weight.** Report the RSS of the frozen tier plus the cost-model tables a
  helper (ticket 07) would hold, against the ~4 GB a whole sim worker holds. The map's fog
  says a helper that replicates the sim is unaffordable at 16 workers on 128 GB; this number
  decides whether ticket 07 can exist.

## The gate

CLAUDE.md section 1: instruments outside a gate have rotted three times here. Add a
SMALL-shape smoke form to the pytest selection asserting only non-vacuity -- same sequence
both ways, at least one unit seated -- so the file cannot quietly stop measuring. The
campaign-shape timing stays behind a flag: a judgement instrument, not a pass/fail one.

## Bar

The bench prints the three timings and the replica RSS at the campaign shape; the smoke form
is in the CLAUDE.md selection and green; `phase-2-campaign` 04 is resolved with a pointer here.

## Answer

Resolved 2026-09-22.

`Tests/bench/bench_pool_open.py` builds the campaign shape (1,400 aisles x 3 brackets x 6 bins =
25,200 bins, a 12-unit load of distinct SKUs, a 10% taken set) through the production
`freeze_tier`, and times the prologue, an eager open-and-seat and a template open-and-seat for
BOTH families the phase-2 winner pair runs (`_TravelBalancedPool`, `_MinLaborPool`). It raises
if the eager and template opens emit different `(aisle, x, y, score)` sequences. The per-open
state deep-copy is timed alone and subtracted, because the evaluator reads a copy-on-write view
and never pays it.

First reading on this machine, median of 20:

| | measured | on record (2026-09-20) |
|---|---|---|
| `aisle_buckets()` prologue | 4.02 ms, 29% walk / 71% cursors | 3.685 ms, 33% / 67% |
| travel-balanced open, eager / template | 20.9 / 15.5 ms, 1.35x | 13.35 / 9.23 ms, 1.4x |
| min-labour open, eager / template | 30.0 / 24.6 ms, 1.22x | (never measured) |
| replica weight | 13.6 MiB for 25,200 bins, ~566 B/bin incl. pool inputs | -- |

The shape and the ratio reproduce the record; the absolute opens run ~1.6x higher than the
throwaway script's, which held fewer SKUs per aisle (this scene seeds three per aisle so the
co-occurrence and ledger paths are live). Read the ratio and the split, not the milliseconds.

**For ticket 07:** a helper holding the frozen tier and the pool inputs weighs tens of MiB at
this shape, not the ~4 GB a sim worker peaks at; `--replica-bins N` scales it to a campaign's
real empties count. A replica is affordable; whether it is needed is ticket 04's question.

**The gate.** `Tests/unit/test_bench_pool_open.py` (small shape, 0.5 s) joined the CLAUDE.md
pytest selection: the two opens emit the same sequence and seat something, the template store is
actually used, the walk replica finds the real prologue's aisle order, and a divergent sequence
makes `measure` raise. Timing stays a judgement read, never a pass/fail.
