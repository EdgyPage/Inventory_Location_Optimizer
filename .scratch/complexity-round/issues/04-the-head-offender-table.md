# The HEAD offender table

Type: research
Status: resolved

**No archived artifact may be cited as a HEAD number, and this ticket exists because of that.**

`Tests/calltree/out/index.json` holds 38 artifacts. Every one predates `bd29d2eb` -- the `take`
heap that removed the O(A) per-placement scan from `_TravelBalancedPool` -- so the single most
prominent name in the archive (`_aisle_best` / `_score_of` at k = 1.55) describes code that has
since changed shape. Two further reasons nothing in there is quotable:

* the `yard`-knob artifact's x axis is `[2.2938, 2.2938, 2.2938, 2.3723, 2.3723]` -- a 4% range
  with duplicates, the mis-calibration `.scratch/inbound-performance/issues/05` retracted;
* every `t_sample` exponent describes the `v1` sampler, retired 2026-09-12 (ticket 02).

## Preconditions, and why each is checked rather than assumed

1. **The instrument is green on HEAD.** `python -m pytest Tests/calltree -q` -> **26 passed,
   exit 0, 456 s**. This is not ceremony: the tier is in no CI gate, and it was
   1-failed/21-passed on clean `develop` once before, from a frozen oracle re-priced in
   production and not in the test tier.
2. **The offender table is readable** -- ticket 01.
3. **The fixture runs production's configuration** -- ticket 02.
4. **A quiet host.** Four samples of identical work once gave run differences of +19.7, +24.0,
   -0.6 and +1.4 s. The phases below run sequentially for this reason.

## What is being measured

    python -m pytest Tests/calltree -q                                          # re-confirm green
    python Tests/calltree/calltree_growth.py --ladder meso --knob skus
    python Tests/calltree/calltree_growth.py --ladder meso --knob skus --config split_staging4

`--config` matters and is not an optional extra: until 2026-08 no ladder set `put_timing`,
`put_split`, `put_staging` or `recv_crew`, so `_admit_held`, `_held` and `HeldItems` were
**structurally dead in every rung** and a quadratic living there was reported as clean.

## Checks before any exponent is read

Recorded here so the numbers cannot be read past them:

* **the x axis actually moved** -- a 4% range is noise dressed as a law;
* **intermediate counts differ** between rungs. A ladder whose rungs exceed its fixture's declared
  size runs the SAME catalogue and prints different ratios with no warning;
* **`flows: ALL ZERO` means NOT MEASURED**, not "the path never ran". A level read at the end of a
  run is not coverage; a flow is.

## Answer

Run 2026-09-16 on `608fbfc9`, sequentially on a quiet host. Archived as
`growth__cfg-none_knob-skus_ladder-meso_seed-42__20260916T153644Z_608fbfc9e497.json` and
`growth__cfg-split_staging4_..._20260916T154035Z_608fbfc9e497.json`.

`pytest Tests/calltree -q` re-confirmed green with the era sampler: **33 passed, 434 s**.

### The prediction from ticket 02 held

Recorded before the run: *"`t_sample` leaves the offender table."*

| | archived (v1) | HEAD (v3) |
|---|---|---|
| `t_sample` | **k = 1.522** | **k = 1.11** (cfg=none), **1.09** (split_staging4) |

It is no longer an offender under either config. The retired sampler was the whole finding.

### The offender table on HEAD

Nothing is a SECTION-WALL offender: the highest is `t_reord` at k = 1.48 against a 1.50 flag --
close, and it is the section placement happens in, so it corroborates rather than convicts. Every
conviction below is a CALL COUNT, which is the sharper early-warning instrument and is not itself
a cost.

**cfg=none** (xs 500..8,000):

| offender | k | counts at 8,000 |
|---|---|---|
| `AffinityStore.delta_lift_idxs` | **1.59** | 157,981 |
| `_TravelBalancedPool._aisle_best` | **1.54** | 427,497 |
| `_TravelBalancedPool._score_of` | **1.54** | 427,497 |
| `delta_lift_idxs.<locals>.<genexpr>` | 1.52 | 1,581,454 |
| `sum_lift.<locals>.<listcomp>` | 1.32 | 29,715 |

**cfg=split_staging4** (xs 300..2,400) adds one the archive never showed:

| offender | k | counts at 2,400 |
|---|---|---|
| `_TravelBalancedPool._aisle_best` / `._score_of` | **1.57** | 289,531 / 289,310 |
| `delta_lift_idxs.<locals>.<genexpr>` | 1.49 | 335,296 |
| `delta_lift_idxs` | 1.43 | 32,746 |
| **`cost_model:per_pick`** | **1.31** | **542,781** |

### The mechanism, and it is ONE mechanism

A raw count exponent cannot separate "more units" from "more work per unit"
(`[a count is not a claim]`). Dividing by the run's own placements:

| | per placement | k |
|---|---|---|
| `_aisle_best` | 0.366 -> 0.474 -> 0.716 -> **1.205** | **0.573** |
| `delta_lift_idxs` | 0.157 -> 0.264 -> 0.366 -> **0.445** | **0.502** |
| `per_pick` (ss4) | 5.298 -> 6.775 -> **9.409** | **0.414** |

Work per unit IS growing, and all three grow at the same rate, because they are the same thing:
**every family re-scores EVERY AISLE at every SKU-run boundary, so per-unit cost tracks the aisle
count.** `_aisle_best` and `_score_of` have identical counts because they are called 1:1;
`per_pick` sits inside `_aisle_best`, once per (aisle, height bracket), which is why its
per-placement figure is ~8x theirs.

### What this REFUTES

**`_aisle_best` was not fixed by the `take` heap.** It is still convicted on HEAD at k = 1.54 with
427,497 calls, and its per-placement cost more than TRIPLES across the ladder. `bd29d2eb` removed
the O(A) SCAN from `take`'s selection -- the argmin over aisles per placement. It did not touch
the run-boundary rebuild, which calls `_aisle_best` once per aisle every time the SKU changes.
That is the `R x A` term, and this is the first HEAD measurement confirming it survived.

### What this makes of the refactor queue

The plan's B2a (`_RankedAssignPool.take`, ticket 03) is **not** the top candidate by this table --
it does not appear in it at all under either config, because these rungs run the default strategy
rather than the ranked-assign arms. That is a coverage gap in the ladder, not evidence of
innocence, and it must be said plainly rather than read as an acquittal.

What IS convicted, on HEAD, twice, under both configs, is the `_TravelBalancedPool` run-boundary
rebuild and `delta_lift_idxs` -- the plan's B2j and B2d. `per_pick`'s 542,781 calls are the
memoizable part of B2j that `INBOUND_PERF_FINDINGS.md` already named: it is called once per
(aisle, bracket) for a value depending only on `(m, var)`.
