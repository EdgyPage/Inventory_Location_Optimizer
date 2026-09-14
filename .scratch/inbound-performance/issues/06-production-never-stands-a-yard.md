# Production never stands a yard, so the quadratic is not where phase 2's cost is

Type: research
Status: open

This retracts the ordering of this effort's own refactor queue. Recorded the moment it was
measured, because everything upstream of it was built to convict a term that does not bite.

## The measurement

The repaired fullfid tier — the REAL driver (`_run_strategy_worker`), the real generated
catalogue, a standing yard, `gain_forecast` on both knobs — with `plan_order`, `place_load` and
`_make_pool` counted directly:

```
PRODUCTION-SHAPED RUN  policy=gain_forecast skus=1000 batches=15 coverage_days=5.0 recv_crew=4
  plan_order entry calls     : 28
  place_load calls           : 80
  place_load per entry       : 2.9  -> implied mean T = 1.26
  candidates per entry       : mean 1.2  max 2  (non-empty: 28 of 28)
  depth histogram            : 1:22  2:6
```

**The yard never exceeds two trailers.** `plan_order` costs T(T+1) `place_load` calls, so at T=2
that is six. The O(T^2) greedy is running in a regime where the quadratic is indistinguishable
from a constant.

## Why, and it is structural rather than incidental

`RECV_DAY_SECONDS = None` in `Optimization/config/settings.py:116` — and the comment states it:
*"None = no whistle"*. It is not derived under the era (no reference in `simconfig/` or
`simdriver/`), and `PHASE2_RUN_DEFAULTS` does not set it.

Combined with the mechanism established in ticket 04 — `_unload_split`'s loop has exactly one exit
that is not "nothing workable anywhere" (`receiving.py:1078`) — **with no whistle every freed door
immediately pulls the next yard trailer, so the yard empties inside every drain.** Doors, crew size
and trailer type are all non-levers without it.

So the phase-2 campaign, as declared, runs with a yard that structurally cannot stand.

## What this retracts

Ticket 03 and the effort's plan both ranked `_Evaluator._make_pool` as refactor #1, on the
reasoning that `plan_order` is O(T^2) and `_make_pool` is called inside it per tier per virtual
placement. That reasoning is sound and the meso measurement confirmed it — at T=26 the fan-out is
733 `place_load` calls per entry and 65,496 pool opens.

**But T=26 was a property of the fixture, not of production.** The meso runs reached it only under
a hand-set whistle that production does not have. At production's T=1.26 the same code does six
`place_load` calls per entry call.

**The convicted term is real and is not the cost.** Both halves matter: a future run that DOES set
`RECV_DAY_SECONDS` — or a bigger site, or a slower crew — walks straight into the quadratic, so
the finding keeps its value as a standing risk with a named trigger. It is simply not what phase 2
is paying for today.

## The contradiction that has to be resolved next

`inbound-optimization` ticket 31 measured a gain cell at **1.6-1.9x** an unpriced one on a real
coupled run — the gain bundle adding 441-477 s per unit on the `fifo` arms and 326-854 s on `tmin`.
Eighty `place_load` calls cannot cost 450 seconds. Both measurements are sound, so the cost is
somewhere the candidate count does not reach.

The candidate explanation, and the next thing to measure: **the evaluator's cost is per UNIT, not
per candidate.** `place_load` is called once per candidate trailer, but its body works through that
trailer's whole load — and `_place_pool` opens a pool per TIER per placement, measured at ~5 pool
opens per `place_load` even at small T. A 53-foot trailer carries 26 pallet positions, and at
production scale each position holds many units. So the shape is roughly

    cost ~ entries x units_per_trailer x tiers

with T entering only as a multiplier on the entry count, not as a square.

If that is right, the refactor queue reorders: `_make_pool`'s per-tier re-open, `_avail` /
`_tier_sorted`'s per-placement rebuilds and the per-unit pricing move ahead of anything about
`plan_order`'s candidate loop — and the copy-on-write fix for `_make_pool` stays #1 but for a
completely different reason than the one it was chosen for.

## Caveats this measurement carries, stated before anyone leans on it

1. **Scale.** 1,000 SKUs, 15 batches, `coverage_days=5`. The campaign is 40 site days on a far
   larger catalogue. Yard depth could grow with scale; nothing here rules that out.
2. **Store leaf only.** `run_fullfid` takes `_channel_runs[0]`, which `workunits.py:1256`
   guarantees is the store — and store binds 14-17 of 75 drains against fulfillment's 46-60. This
   is the quieter half of the site.
3. **Uniform adapter.** `_make_pool` reported ZERO calls, which is the probe's limitation and not
   a property of production: the default `strategy_args[0]` is `uni_fifo_norsl`, and `fifo`
   selects the uniform adapter, which opens no pool at all. A pool-adapter re-run is in flight.
4. **Uncoupled.** No `--couple-channels`, so `PutawayPool`, the two-leaf `compose_site_view` and
   `_unload_split`'s door teams were not exercised.

None of these weakens the structural half of the finding — `RECV_DAY_SECONDS = None` is a
declaration, not a scale effect — but every one of them limits the numeric half.

## Answer to the contradiction: the cost is the TIER loop and the LOAD, measured

Same tier, same catalogue, but a POOL adapter (`uni_rank_labor_norsl`) so `_make_pool` actually
fires, at double the scale:

```
PRODUCTION-SHAPED RUN  policy=gain_forecast skus=2000 batches=25 coverage_days=5.0 recv_crew=4
  plan_order entry calls     : 48
  place_load calls           : 590      -> 12.3 per entry, implied mean T = 3.04
  _make_pool calls           : 7,426    -> 12.59 pools per place_load
  UNITS offered to place_load: 94,274   -> 159.8 units per call
  candidates per entry       : mean 2.8  max 6
  depth histogram            : 1:8  2:12  3:14  4:11  5:1  6:2
```

**T held small — 3.04, max 6 — at double the SKU count and 67% more batches** than the first run's
1.26/2. So the structural finding survives a scale check: the yard does not stand, and it does not
grow its way into standing.

**And `_make_pool` still fired 7,426 times.** The decomposition is
`48 entries x 12.3 place_loads x 12.59 pools = 7,426`, and the middle term is the only one T
touches. The multipliers that matter are:

| factor | measured | what it is |
|---|---|---|
| tiers per placement | **12.59** | `_place_pool` opens a fresh pool per BinKey in the spill chain |
| units per placement | **159.8** | one `place_load` works a whole trailer load |
| candidates per entry | 2.8 | the only term T contributes, and it is nearly flat |

So the cost shape is **`entries x T x tiers`** for the opens, with the per-unit pricing riding
inside each one — and T enters linearly, as a small multiplier, never as a square.

### What this does to the refactor queue

`_Evaluator._make_pool` stays **#1**, and the copy-on-write fix stays the right fix — but the
reason is now the TIER loop (`gain.py:947-962`), not `plan_order`'s candidate loop. That changes
what the guard has to prove and what the post-fix exponent should be fitted against: the
prediction is that pool opens fall from ~12.6 per placement toward ~1, with the candidate count
irrelevant. A ladder fitted against yard depth would show nothing either way, which is exactly why
the first one found nothing worth having.

`plan_order`'s per-candidate set algebra (the old #4) drops off the queue: at T=3 there is nothing
there to win.

Everything per-PLACEMENT or per-UNIT moves up, because those are the terms with the large
multipliers: `_avail` / `_tier_sorted`'s per-placement rebuilds, `_place_pool`'s re-filter of
`live` on every tier iteration, and `_place_merge`'s per-placement sort of a ~160-unit group.

### Still unmeasured

The 1.6-1.9x multiplier itself is not yet reproduced from these counts — 7,426 opens over 25
batches on one leaf is a number, not a wall. Converting it needs either a traced capture with
`t_inbound` carved (the instrument now supports it) or a same-day priced-vs-unpriced control at
this scale. Until then the ATTRIBUTION above is measured and the MAGNITUDE is not.
