# 04 - nothing between the toy digest and a three-hour probe can price a pool open

Type: task
Status: open

**Found 2026-09-20** while landing the performance round (commits `c9a447af`..`679b18f6`). The
instruments this repo has for the gain evaluator's pool path are:

| instrument | cost | what it answers |
|---|---|---|
| `run_digest.py` + `--spec _toy_priced` | ~90 s | *did results change* — blind to cost by design |
| `calltree_growth.py --knob yard --config inbound_gain_pool` | minutes | *what is the call shape* |
| `--spec _probe_unload_ref` at campaign scale | **~3 h** | the only thing that can price an open |

There is nothing in between, and the fast one **gave the wrong sign**. The meso ladder's tier
carries 74 buckets against the campaign's 4,200, and its round has ~3 candidate trailers against
25, so it rebuilds a per-round template almost as often as it reads one. It scored the
round-shared pool prologue (`db300f38`) at **+169,343 traced calls — a regression** — for a change
that is **1.4x faster** at the campaign shape. Memory:
`meso-ladder-cannot-size-the-pool-prologue`.

So the only honest way to size a pool-open change today is a three-hour run, which is why this
round's shape decision (memoise the SHAPE vs open over a copy-on-write TEMPLATE) had to be made
from a throwaway script.

## What to build

`Tests/bench/bench_pool_open.py`: a synthetic `FrozenTier` at the measured campaign shape — 1,400
live aisles x 3 height brackets x 6 bins = 25,200 bins — through
`Assignment_Functions.freeze_tier`, reusing `Tests/unit/test_frozen_tier.py`'s `_wp` / `_bins`
fixture idiom (its column x height grid plants the D ties that make the tie-breaks real). Time:

1. `TierSlice.aisle_buckets()` alone, split into (a) the first-live walk + sort and (b) the
   `_Cursor` constructions. Read **3.685 ms**, 33% / 67%, on 2026-09-20.
2. open-and-seat through a real `_TravelBalancedPool` over an eager slice: **13.35 ms**.
3. the same over a shared template store (`tier.slice(excl, store)`), which is what a round of the
   greedy now does: **9.23 ms**, 1.4x.

**Assert the emitted `(aisle, x_phys, score)` sequence is identical between (2) and (3)**, so the
benchmark is also a correctness check and cannot silently time two different computations.

## Why it needs a gate, not just a file

CLAUDE.md section 1 records that instruments outside a gate have rotted three times here, and
`hand-run-test-tiers-rot-silently` is the same lesson. Add a SMALL-shape smoke form to the pytest
selection in CLAUDE.md section 1 asserting only non-vacuity — same sequence both ways, at least
one unit seated — so the file cannot quietly stop measuring. Keep the campaign-shape timing behind
a flag: it is a judgement instrument, not a pass/fail one.

## Why it is worth doing now rather than later

A cProfile of what a pool open costs AFTER this round puts **72% in
`_TravelBalancedPool._aisle_best`** — 11,212 calls per open, live aisles x SKU-run boundaries, to
seat ~12 units (memory `aisle-best-is-what-a-pool-open-now-costs`). That is the next target and it
is a large one. Without this benchmark, every attempt at it costs a three-hour run to score.

Working drafts from the round are NOT in the repo; they were written to a session temp directory
and are gone. The numbers above are the record.
