# The ladder measures a sampler production retired four days ago

Type: task
Status: resolved

Found while auditing config duplication for `config-centralization`, not by looking for it here
-- which is the point. Nothing in the instrument could have reported it.

## The defect

`Tests/calltree/calltree_scenarios.py` builds its `BatchConfig` like this:

    batch_cfg = BatchConfig(inventory_size=len(inventory.orders),
                            mean_fraction=0.15, std_fraction=0.05)

No `sampler`, so it takes the dataclass default **`'v1'`** (`Workload_Builder.py:74`).
Production declares **`settings.SAMPLER = 'v3'`** and has since 2026-09-12.

The `'v1'` default is NOT a bug. `channels.py` records the decision: *"default here matches
BatchConfig's own 'v1' so non-runner constructions (tests, Diagnostics) keep their frozen
historical meaning; the runner passes the era in."* That is right for a unit test pinning an old
batch sequence. It is wrong for an instrument whose entire job is to describe what production
costs.

`Batch.__init__` is `SECTION_MAP`'s anchor for **`t_sample`**. So every `t_sample` exponent in
the archive describes a sampler no run executes.

## The measurement

Direct, isolating the sampler from everything else -- `k = 0.15*N`, `affinity=None`, five rungs
500..8,000, the same shape the meso `skus` ladder uses:

| sampler | k | r^2 | walls (s) |
|---|---|---|---|
| **v1** (the fixture's) | **1.477** | 0.993 | 0.0006, 0.0013, 0.0036, 0.0108, 0.0359 |
| **v3** (production's) | **0.822** | 0.901 | 0.0005, 0.0004, 0.0008, 0.0017, 0.0038 |

**9.6x apart at N = 8,000**, and the gap widens with N by construction: v1 is O(k*N) (a cumsum
over every candidate per draw), v3 is O(k*log N) (a segment tree).

The archived meso `skus` ladder fits **`t_sample` at k = 1.522**. Against v1's independently
measured 1.477, the attribution closes.

**Caveat, stated because this document's ancestors were burned by not stating one:** the table
above passes `affinity=None`, so partner lift effects are absent, while the real `Batch` path
passes an affinity store. The O(k*N) cumsum is the dominant term either way and the fitted
exponents agree to 0.05, so the attribution holds -- but these are not the ladder's own seconds
and must not be quoted as such.

## What lands

`build_assets(..., sampler: str | None = None)`; `None` resolves to `settings.SAMPLER`, so the
fixture follows the era by construction rather than by anyone remembering. Passing `'v1'`
explicitly still reproduces an archived artifact, which is what the frozen-meaning default was
protecting.

While there: the scenario's own `fee_threshold_days=2.0` / `urgency_horizon_days=0.0` defaults now
read `Inbound/gain.py`'s declarations, for the same reason `config-centralization` ticket 01 gave.

## Prediction, recorded BEFORE the re-run

`t_sample` leaves the offender table -- v3's 0.822 is below `FLAG_TIME_EXP = 1.50` with room to
spare. If it does not, the attribution above is wrong and this ticket is retracted, not amended.

## The switch moves NO measurement -- and that corrects the source

Written expecting to have to justify a new baseline. The measurement says otherwise.

At **2,000 SKUs over ten seeds**, v1 and v3 draw **the same SKUs, the same quantities, and in the
same order** -- `b1.items == b3.items` and `list(b1.items) == list(b3.items)`, exactly. Same at
150 and 600 SKUs, six seeds each.

They agree because both implement one selection rule -- *the first index whose cumulative weight
passes the draw* -- and both consume one uniform per draw. They part company only where v1's float
accumulation does, which is the **~1e26 weight dynamic range of the production catalogue**, not a
synthetic fixture whose weights are benign. That is the same mechanism the v2 defect turned on
(catastrophic cancellation), reported from the other side.

**So `Workload_Builder.py`'s comment that v3 "moves every batch sequence" is not true at fixture
scale.** It is true where it was written to be true -- a production run -- and this ticket does
not touch that. But it is why the comment must not be read as "any switch is a new baseline".

The consequence is that this fix is strictly better than a baseline change: pointing the scenarios
at the era moved **the cost of drawing a batch** (k 1.477 -> 0.822, 9.6x at N=8,000) and **not the
batch**. Count exponents stay comparable with the archive; only the spurious `t_sample` wall
exponent goes away.

`test_switching_the_scenario_sampler_moves_no_measurement_at_fixture_scale` pins this, and its
docstring says what a failure would MEAN: the era switch has become a comparability break at
fixture scale and the archived count comparisons stop being valid. That is a finding, not a
broken test.

## Consequence for the archive

Every archived `t_sample` exponent is about retired code. This is the THIRD time a ladder here
has measured something other than what it claimed -- after `--config` leaving the put-away path
structurally dead in every rung, and the catalogue ceiling silently truncating the top rungs. The
shape is identical each time: **the fixture and production diverged, and only the fixture was
ever read.**
