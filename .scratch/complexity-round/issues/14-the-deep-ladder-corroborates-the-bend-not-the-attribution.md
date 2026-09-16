# The deep ladder corroborates the BEND, not the attribution

Type: research
Status: resolved

The first full-span deep ladder on HEAD: **5 rungs, 10,000 -> 80,000 SKUs (8x), 136 arms per
rung, on the 400,000-SKU catalogue**, unblocked by `--profile-run`. Walls 8.2 / 10.5 / 18.2 /
27.0 / 36.4 min.

It was run to test ticket 07's projection -- that the `R x A` rebuild, worth 2.66% of a meso rung
at 2,400 SKUs, climbs to ~74% at campaign scale. **It half-answers, and the half it refuses is
the important one.**

## What it establishes

`reord_s` -- the section placement lives in -- fits **k = 1.12, r2 = 0.99**. But a single fit
hides the shape, and the LOCAL exponents between consecutive rungs rise monotonically:

    reord_s      0.88  ->  1.13  ->  1.31  ->  1.38        RISING
    save_s       1.20  ->  0.94  ->  0.93  ->  0.96        falling
    sim_s        0.91  ->  1.02  ->  1.13  ->  1.09        flat
    build_s      0.91  ->  0.99  ->  1.07  ->  1.08        flat
    extract_s    0.72  ->  0.90  ->  1.06  ->  0.97        noisy

A pure power law has CONSTANT local exponents. **A super-linear component is taking over inside
`reord_s`, it is doing so monotonically, and the rise is specific to that section** -- every other
per-arm total is flat or falling. The headline k = 1.12 understates the top-end behaviour by a
wide margin (1.38 between the last two rungs).

## What it REFUSES to establish, and this is the finding

The rise is **not** attributable to the `R x A` rebuild by this data. Fitting `reord_s(n) = A*n +
B*n^q` with `q` FIXED at 1.912 -- the exponent independently measured for the rebuild on the meso
ladder -- fits **worse than a plain power law**:

| model | worst rung error | log residual |
|---|---|---|
| single power law, k = 1.12 | 8.6% | 0.0249 |
| **linear + rider at q = 1.912 (the measured R x A exponent)** | **16.9%** | **0.0354** |
| linear + rider at q = 2.60 (q fitted freely) | 9.2% | 0.0098 |

The free fit prefers a steeper, later-onset rider (q = 2.6, share 1.4% -> 29.8%) -- but that is
three free parameters over five points, and its -9.2% miss at the bottom rung says so.

**So: `reord_s` bends, and the bend is real and specific. The claim that the bend IS the `R x A`
rebuild is not supported.** `reord_s` contains the entire reorder phase -- the reorder ledger, the
queue, the DB writes -- not just the pool's aisle selection, and any of those could carry it.

## Consequence for ticket 07

Ticket 07's **measurement** stands unchanged: the rebuild is `R x A`, k = 1.912, decomposed
exactly (1.045 + 0.867), worth 2.66% at 2,400 SKUs. Those are meso-tier call counts, exact and
deterministic.

Ticket 07's **projection** -- ~12% at 24k, ~53% at 240k, ~74% at campaign -- was flagged there as
"a PREDICTION, not a result... extrapolating a 4x-span fit out to 167x". This ladder is the test
that projection asked for, and the verdict is: **the direction survives, the attribution does
not.** Something in `reord_s` grows super-linearly and reaches a substantial share by 80,000 SKUs.
Whether it is the rebuild is unmeasured, and the one model that assumes it is fits worse than
assuming nothing.

**Do not quote the 74%.** Quote the bend.

## What would settle it

The rebuild's own call count, per rung, on the deep tier -- `rebuild_calls` as a `_FLOW_COUNTS`
entry rather than an inference from a section wall. The meso probe already counts it (ticket 07);
the deep tier does not, because its sections come from each run's own log rather than a tracer.
That is a real gap and it is what any further work here should close first.

## Also measured

**Commensurability improves with scale but never closes**: `sum(total_s)/workers` against the
measured wall runs 0.26 / 0.40 / 0.47 / 0.50 / **0.52**. Even at the top rung, **half the wall is
not per-arm work** -- spawn, sqlite, and 18 workers draining unevenly against 16 physical cores.
Anyone reading a deep-tier wall as "the cost of the simulation" is reading roughly double.
