# RETRACTED: "a pool-adapter gain cell costs ~1.1x a run"

> **RETRACTED 2026-09-14 by ticket 13.** At the campaign catalogue size and rho the
> same measurement reads **3.24x**, not 1.1x. Everything below was measured on a
> ladder that could not reach that regime: the store leaf never stands a yard
> (T 1.14-1.30), and the coupled ladder that followed was capped at T = 2.25 by its
> catalogue (ticket 12). The campaign runs at **T = 12.97**, and the drain is cubic
> in T.
>
> **What survives is the METHOD, and it is the reason the error was findable:** two
> multipliers with different denominators, paired within a rung, with the
> commensurable one named. The DRAIN/RUN distinction below is correct and still the
> way to read this tool. What was wrong was concluding from a flat RUN column that
> the cost does not grow, when the ladder had simply stopped growing T.
>
> The "honest next step" this ticket ends on was the right one, and running it is
> what produced the retraction.

Type: research
Status: resolved (retracted -- see ticket 13)

The user's direction was "measuring growth on multiple smaller inventories should give you the
information you need without a complete run". This is that measurement, and it lands on a number
the campaign can use.

## What was built

`Tests/calltree/calltree_inbound_ladder.py`. Per rung it runs the REAL driver twice under the
calibrated era -- once with `fifo/fifo` (the unpriced control) and once with `gain_forecast` on
both knobs (the priced pole) -- on a POOL adapter, and reports the ratio paired within the rung.

Both poles run the standing yard and the space timeline. The ONLY difference is whether a gain arm
ranks, which is what makes the ratio attributable to pricing rather than to the yard existing.

Paired within a rung because `inbound-optimization` ticket 31 section 5 established that no
absolute wall survives a comparison across runs here -- an identical command ran 3.4x apart on two
occasions.

## The result

`--rungs 5000 10000 20000 --batches 10 --arm uni_rank_labor_norsl`, coverage 5, recv crew 4,
store leaf, uncoupled:

| skus | drain unpriced | drain priced | **DRAIN x** | run unpriced | run priced | **RUN x** | T |
|---|---|---|---|---|---|---|---|
| 5,000 | 0.014 s | 0.202 s | **14.28** | 21.7 s | 23.4 s | **1.08** | 1.30 |
| 10,000 | 0.019 s | 0.252 s | **13.40** | 31.0 s | 34.6 s | **1.12** | 1.14 |
| 20,000 | 0.028 s | 0.516 s | **18.26** | 43.7 s | 47.7 s | **1.09** | 1.14 |

**Two multipliers, and they are not the same number.** The evaluator is 13-18x the unpriced DRAIN --
which is a large, real attribution, and is exactly the cost the copy-on-write views and the `live`
guard attack. But the drain is a small part of a run, so the same pricing is **1.08-1.12x** of the
whole run.

**Both are flat across a 4x catalogue range.** Neither multiplier is growing with size at these
scales, and T sits at 1.14-1.30 -- the yard barely stands, consistent with the era measurements in
ticket 06's correction.

## What this does and does not say about phase 2

**It does NOT contradict ticket 31.** That measured 1.6-1.9x on a COUPLED unit, 40 site days, the
reference catalogue, arms `('fifo','tmin')`. This is a store leaf, 10 batches, 5k-20k SKUs, one
pool arm. Different regimes, different denominators, and the honest reading is that they are not
yet comparable rather than that either is wrong.

**What it does establish** is the shape ticket 31 could not: a POOL adapter -- the family eight of
`PHASE2_PAIRS`' twelve arm-slots use, and the one ticket 31 explicitly flagged as unbracketed --
does not blow the multiplier up at these scales. The worry that motivated this whole effort (that
the campaign's 8.6-9.7 h sizing rests on the two cheapest adapters and could be badly low) is not
supported by anything measured here.

**The denominator trap is recorded in the tool itself.** Reading the DRAIN column against ticket
31's band would report a 13-18x campaign multiplier, which is the same class of error as the
101x `SUM(cut)` and the `t_*`-means-against-a-phase-wall incident. The script prints both columns
and says which one is commensurable.

## Caveats the ladder cannot shed, stated in the tool's own output

1. **Store leaf only.** `run_fullfid` takes `_channel_runs[0]`, which `workunits.py:1256`
   guarantees is the store -- and store binds 14-17 of 75 drains against fulfillment's 46-60. This
   is the quieter half of the site.
2. **Uncoupled.** `PutawayPool`, the two-leaf `compose_site_view` and `_unload_split`'s door teams
   are not exercised.
3. **Scale.** 5k-20k against the campaign's 400,000 SKUs (`MAX_SKUS = None`) at 40 site days.
4. **rho is not controlled.** `crew_size` takes a `ceil`, so small rungs float the receiving crew
   far under its target and `rho(N)` is a sawtooth. The multiplier must be read against the
   measured T, never against the rung's SKU count.

## The honest next step

The one measurement that would close this is the same ladder run COUPLED at rungs reaching
rho ~ 0.82 (first around 160k SKUs, per the era investigation's projection). That is a bigger run
but still far short of the campaign -- and it is now a command rather than a build, which is what
this effort was for.
