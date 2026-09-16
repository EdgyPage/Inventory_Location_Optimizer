# The deep ladder was unrunnable on HEAD, and reported a clean bill

Type: task
Status: resolved

Found by running it. Three defects, and the third is the one that matters.

## 1. Every rung failed -- the bin caps bind below the declaration

Each rung carried `--s-max-bins` / `--ff-max-bins`, and all three runnable rungs exited 1 with
`UnfieldableRequirement`: the caps sit BELOW the era's declared stock levels, so the planner
refuses rather than fielding under the line floor.

This is documented behaviour, not a regression. `INBOUND_PERF_FINDINGS.md` records `run_fullfid`
refusing for exactly this reason and concludes **"NO cap value would have worked"** -- a smaller
warehouse raises lines/day, which grows the levels, which needs more bins. The memory
`a-bin-cap-is-self-defeating` says the same: *use `--coverage-days`*.

So the rungs now shrink with `--coverage-days 2.0` (production default is 10), which lowers the
DECLARATION and brings the warehouse down with it. One rung proved out before the full ladder was
run again -- the step skipped the first time, which cost the whole hour.

## 2. The catalogue ceiling, again -- the meso fix was never ported

`run_deep_ladder` shells out `run_simulation --max-skus N` with no catalogue selection, so every
rung binds `find_latest_db_pairs()[0]`: the most RECENT pair, not one big enough. On this machine
that declares **40,000 SKUs** while the top rungs ask for 60,000 and 80,000, and `--max-skus`
above the catalogue is neither an error nor a warning -- it takes everything.

`INBOUND_PERF_FINDINGS.md` records what that costs: three rungs with identical priced quantities,
read as a trend, *"one run measured three times"*. The fix applied there -- letting the
DECLARATION pick the fixture -- went into the meso tier only.

The ladder now sizes itself to the catalogue, DROPS the rungs it cannot serve while naming them,
prints the surviving span, and refuses when fewer than three remain. A `--profiles-dir`
passthrough is added but **cannot reach the 400k catalogue on this machine**: `run_simulation`
takes a profiles ROOT and picks `latest()` inside it, with no flag to name a run. So the ladder
runs 10k -> 40k, a **4x span** -- enough to corroborate direction at real scale, NOT enough to
settle the campaign-scale projection for the `R x A` term.

## 3. IT REPORTED CLEAN ANYWAY -- and this is the finding

After all three rungs failed, the ladder printed

    section exponents [?] (expect ~1 vs max_skus(deep); flag >= 1.5):
    no super-linear offenders flagged at these thresholds

and archived a JSON. `RUNG FAILED ... continue` dropped each rung, and nothing downstream could
tell **"measured and clean"** from **"never ran"**.

That is the same shape as the ALL-ZERO flows warning fixed earlier in this effort, one level up,
and the exact principle this package's README already states: *a level is not coverage; a flow
proves the path ran.* An instrument that says "nothing is wrong" after measuring nothing is worse
than one that says nothing, because the reader banks it.

`run_deep_ladder` now tracks failures, **refuses to report** when fewer than three rungs produced
data (a log-log fit needs three positive points), warns loudly otherwise, and records
`failed_rungs` in the artifact so a stored result carries its own caveat.

## How long had it been broken?

Unknown, and the ticket will not guess -- but the tier is in **no gate**, its own README says
"nothing here is in a CI gate, that is a standing risk", and three dead frozen oracles plus a
never-executed feature were already found rotting in this directory in 2026-08. The caps predate
the era's declaration-driven sizing, so the first failure is bounded below by that change.
