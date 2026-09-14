# Phase 2 restated: ~13 h at 4 workers, not 8.6-9.7 h

Type: research
Status: resolved

`inbound-optimization` ticket 31 sized the campaign at **8.6-9.7 h at 4 workers** and recorded its
own caveat: the priced/unpriced multiplier behind that number was measured on `('fifo','tmin')`,
the only two adapters that open no pool, while eight of `PHASE2_PAIRS`' twelve arm-slots are pool
adapters. Ticket 13 measured the pool-adapter multiplier at **3.24x**. This is the restatement.

## The control first: ticket 31 reproduces from its own inputs

Before changing an input, the model is rebuilt from ticket 31's published numbers and checked
against its published output. If it did not reproduce, nothing below would count.

```
CONTROL: reproduce ticket 31 from its own inputs
  gmyopic  x1.63: serial  30.8 h   @4w  8.6 h   @6w  6.0 h
  gforecast x1.93: serial  35.3 h   @4w  9.7 h   @6w  6.8 h
  ticket 31 published: serial 30.8-35.3 h, @4w 8.6-9.7 h, @6w 6.0-6.8 h
```

Exact, to the published digit. The model is: 3 unpriced cells + 7 priced, 12 units per cell
(6 rule pairs x 2 stock modes), control 642 s per coupled unit, priced = control x multiplier,
plus 0.88 h of serial fixed cost that no worker count divides.

## The restatement

| basis | mult | serial | @4w | @6w |
|---|---|---|---|---|
| ticket 31 upper (all slots priced as non-pool) | 1.93 | 35.3 h | 9.7 h | 6.8 h |
| **arm-slot weighted (8 pool / 4 non-pool)** | **2.80** | **48.4 h** | **13.0 h** | **8.9 h** |
| all slots at the pool multiplier | 3.24 | 55.0 h | 14.6 h | 10.0 h |
| arm-slot weighted, after the `take` heap (ticket 15) | 2.67 | 46.5 h | 12.5 h | 8.6 h |

**The weighted row is the one to plan on.** `PHASE2_PAIRS` is:

| pair | store arm | fulfillment arm | adapters |
|---|---|---|---|
| 1 | `rank_cartlabor` | `rank_minlabor` | pool, pool |
| 2 | `rank_minlabor` | `tmin` | pool, merge |
| 3 | `rank_labor` | `rank_labor` | pool, pool |
| 4 | `tmin` | `rank_cartlabor` | merge, pool |
| 5 | `rank_random` | `rank_popularity` | pool, pool |
| 6 | `fifo` | `fifo` | uniform, uniform |

Eight pool slots, two merge, two uniform — so 8/12 of the arm-slots pay the measured 3.24x and
4/12 keep ticket 31's 1.93x.

**~13 h at 4 workers, ~9 h at 6.** About 1.4x the planned wall.

## What carries forward unchanged

* **Disk: ~164 GiB.** Ticket 31 measured that pricing costs TIME, NOT MEMORY -- peak RSS identical
  to three digits across priced and unpriced cells (3,762 MiB on the `fifo` arms, 4,470/4,502 on
  the `tmin` arms, in every cell). The campaign stays RAM-bound at ~4.5 GiB/worker exactly as
  budgeted, so **6 workers remains an unpriced lever** and is now worth more than it was: it buys
  back 4.1 h against 13.0, where before it bought 2.6 against 8.6.
* **The 0.88 h serial fixed cost** (the freeze and reshape) is not worker-parallel and does not
  move.
* **The ten-cell row is still a LOWER bound for its two futuresight cells**, for ticket 31's own
  reason: futuresight re-aggregates the window on every entry call on top of the same pricing and
  has never been timed.

## What this does NOT establish

The 3.24x was measured on a ladder that differs from a campaign unit in three ways, none of them
closed:

1. **10 batches, not 40 site days.** The ratio is assumed to transfer because both the drain and
   the rest of the run scale with batch count; that assumption is not measured.
2. **One pool arm** (`uni_rank_labor_norsl`), not eight. `rank_minlabor` — store's #2 and
   fulfillment's #1, and the arm that copies the two-level `aisle_member_pos` — was never measured
   separately, and `_gain_bundle_for` calls it "the quiet one".
3. **One fullfid run per pole**, not a full coupled unit with every arm.

So the weighted row is an ESTIMATE built on a measured multiplier, not a measurement of the
campaign. The honest bracket is **12.5-14.6 h at 4 workers**; the lower end assumes the heap's
gain carries and the upper assumes every slot behaves like the measured one.

## The recommendation

Run phase 2 at **6 workers**, budget **~9 h**, and treat 13 h at 4 workers as the fallback. The
memory evidence for the worker lever is ticket 31's own RSS measurement, so it costs nothing that
has not already been paid for -- with the one caution ticket 31 also recorded, that round 2 of the
prior campaign found a synchronized checkpoint storm when many workers checkpoint together, which
is drive-bound rather than RAM-bound and was never re-measured at 6.
