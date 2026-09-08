# Re-read the check under the fielded floor

Type: task
Status: resolved

Graduated 2026-09-08 from the map's **Not yet specified** patch "Re-reading the check", which
became specifiable when [Retire the authored stock levels](22-retire-the-authored-stock-levels.md),
[Field the requirement](23-field-the-requirement.md) and
[Build the empty-first top-up](24-build-the-empty-first-top-up.md) all landed. AFK.

## Question

Take ONE 40-day era run on the reference pair and re-read the check that
[Build the line floor](17-build-the-line-floor.md) failed, through the equilibrium report.

**The spec is the check's own spec** -- `_canary_single` (one cell, `k=1`, loss 0.0, zoning off,
scheduler `round_robin`, arm `fifo`), `--shift-drain-or-cap`, `--n-batches 40`, the
`catalogue_reference_lt0` pair, coverage left at its default. A re-read has to be comparable
clause-for-clause with 17's or it is a different experiment. Whether a RANKED arm needs its own
run is the map's separate fog patch ("Ranked arms' steady-state placement"), not this ticket.

Three things changed under the check since 17 and the run has to be read knowing all three:

1. **The fulfillment warehouse grew and the planner now fields exactly** (23): 977 -> 1,232
   aisles, 0 SKUs below floor and 0 grown on both sections when the check run's own pair was
   re-planned. The treadmill that drove 17's fulfillment leaf (48,466 SKUs -- 30.3% -- below
   their line floor, missed share 0.663 rising, 88,857 units of supply carry, 39/40 capped)
   should be gone BY CONSTRUCTION. If it is not, the cause is somewhere else and this ticket
   names it.
2. **The drain order moved** (24, ADR-0003): put-away fills an empty bin first, picks drain the
   smallest bin first, and the repack rescues are receiving work. No absolute pick / travel /
   throughput number from before 24 is comparable (memory `drain-order-is-smallest-first`), so
   this is a re-read of the CLAUSES against the record's own expectations, never a diff of 17's
   figures.
3. **The audit has a fifth clause** (24): `rework` judges the repack count against the record's
   `f_repack = 0` (provenance `assumed`), and reports own-bin share and free-index depth without
   judging them. No real run has ever shown that reading. Whether a 40-day run at the declared
   levels actually holds zero repacks is the FIRST thing to look at.

Also expected to have moved, and to be read rather than assumed:

- The store leaf's audit should now RENDER instead of raising
  ([Overtime behind a drained day](21-overtime-behind-a-drained-day.md) landed): 36/40 days had
  overtime and `is_drained` now takes it as a fifth required term.
- 17's store cause -- 215,358 of 239,938 SKUs on more than one bin by day 40, missed share
  climbing 0.127 -> 0.283 against 0.095 expected -- is exactly what ADR-0003 was built to end.

## Done when

- The run is taken and completes with no dead arm (check `run.log` for `Traceback`,
  `produced no data`, `Config stage: 0 job(s)` -- memory `pool-run-swallows-dead-arms`).
- Both leaves' audits render, and every clause is read per department per leaf: days drained,
  `released_late` on drained days, utilization in band (pick / put / receiving), missed share
  trend, and `rework`.
- Own-bin share and free-index depth are recorded as OBSERVATIONS -- they are the input the
  map's banding patch waits on, and this ticket does not invent a threshold for them.
- The answer says plainly either that the era's numbers on both sections stop being provisional
  (which lifts [Sequence the inbound funnel](05-sequence-the-inbound-funnel.md)'s hold and the
  three open tickets on the inbound-optimization map), or which department, which leaf and which
  clause failed -- and what that graduates into.

## Answer

**RE-READ TAKEN 2026-09-08, `comparison_20260908_075736` (`_canary_single`, fifo, 40 era days,
the `catalogue_reference_lt0` pair, 16 min wall). 17's two structural defects are GONE and the
new `rework` clause reads clean on its first real run -- but the check still FAILS on both
leaves, on two questions that are now sharp and separable. The era's numbers on both sections
stay PROVISIONAL. Graduated 26 and 27.**

Run health first (memory `pool-run-swallows-dead-arms`): no `Traceback`, no `produced no data`,
`Config stage: 56 job(s)`; stderr holds only four scipy `ConstantInputWarning`s, which are the
two byte-identical fifo arms being F-tested against each other (memory
`fifo-restock-ignores-initial-placement`). `opt_fifo` and `uni_fifo` agree to every digit below.

### What 22, 23 and 24 fixed -- confirmed on a live run

- **The fielding promise holds.** Store fielded 2,510,168 against a declared 2,510,168;
  fulfillment 1,844,275 against 1,844,275; **0 SKUs below their line floor and 0 above their
  declaration on BOTH sections** (17: 48,466 ff SKUs, 30.3%, below floor). The fixed point
  converged in 4 rounds to the same sum Q as 17's check -- with every SKU on the line floor Q is
  a function of the line law alone, so the derived lines/day has nothing to move -- inside a
  **2,341-aisle** warehouse against 17's 2,086. That +255 is exactly the fulfillment growth 23
  measured when it re-planned this pair (977 -> 1,232 ff aisles).
- **The fulfillment treadmill is gone by construction, as 23 predicted.** Missed share 0.663
  RISING (+0.171) -> **0.118 with trend -0.010 in days 20-39, the clause PASSING**; days drained
  1/40 -> 18/40 (12/20 in the window); supply carry 88,857 units standing -> a per-day maximum of
  5,412.
- **Both stamped fill rates are now 0.922** (expected missed share 0.078), store and fulfillment
  alike, because both sections sit 100% on the floor at exactly their declaration. 17's split
  (0.905 / 0.782) was the planner's growth and shrink showing up as a mis-priced shelf.
- **`rework` = ok on both leaves, both windows -- the reading no run had ever produced.**
  **0 packs repacked over 0 rescues; own-bin share exactly 0.000 every single day; free-index
  floor 1,197,833 (ff) / 1,220,376 (store) of 2,096,050 bins**, drifting only ~1.6% over 40 days
  (1,217,382 -> 1,197,833). ADR-0003's `f_repack = 0` (provenance `assumed`) is CONFIRMED: a
  warehouse sized to hold its declaration never made put-away consolidate, so the own-bin rung and
  the receiving rescues never fired. 17's store cause -- 215,358 of 239,938 SKUs on more than one
  bin -- is not merely improved, its mechanism never engaged.
- **The store audit RENDERS** (21 landed): no `InstrumentError`, and `overtime_only_days = 0` on
  both leaves, so overtime never alone capped a day.
- **`released_late` = ok on both.** Every second of lag sits behind a CAPPED day (ff max 260.6 s,
  store max 2,215.4 s); none behind a drained one.
- **Receiving in band on both leaves** (store 0.638 realized / 0.663 expected; ff 0.178 / 0.179).

### Failure 1 -- put-away is priced with ONE site number across two geometries

`utilization` FAILS on both leaves, **in opposite directions, on the same department**:

| leaf | realized | expected | delta |
|---|---|---|---|
| fulfillment | 0.477 | 0.691 | **-0.214** |
| store | 0.362 | 0.155 | **+0.207** |

This is 17's signature unchanged (ff 0.507/0.713, store 0.291/0.134), and the cause is now
measured off `work_events`:

- **realized put-away costs 98.50 s/unit on the store and 29.15 s/unit on fulfillment -- a 3.4x
  spread -- against the record's single site-wide `s_put = 41.24`.**
- The UNITS are right: 6,241/day store and 27,814/day fulfillment against a derived 6,382 and
  28,485, both within 2%.
- The SITE TOTAL is right: 1,425,557 realized s/day against a derived 1,437,958, **-0.9%**. The
  two errors cancel because the unit mix (28k ff : 6k store) drags the weighted mean to 41.86.

So **the put crew of 59 is correctly sized and wrongly apportioned**, and the band the check
tests is exactly the apportionment. The asymmetry is structural, in `simconfig/staffing.py`
`derive`: `s_pick` is per channel (108.371 store / 17.362 ff -- a 6.2x spread the derivation
already respects), receiving takes its per-channel seconds **straight from the script**
(`ch_recv_s = t.per_day(t.recv_s)`, exact because an unload has no travel term), and put-away
alone crosses the section boundary with one price -- `ch_put_s = t.per_day(t.put_units) * s_put`.
Put is the only one of the three departments whose expectation multiplies per-channel units by a
site-wide constant, and it is the only one out of band. -> [Give put-away a per-channel expected
travel](26-give-put-away-a-per-channel-price.md).

### Failure 2 -- the store does not reach a steady state in 40 days

`drained` FAILS on both leaves, but only the store's is a trend rather than a level: fulfillment
18/40 (12/20 in the window), store **5/40 and 0/20 in the measured window, all 20 capped**.

The store degrades across the run while its unit cost stays flat:

- picking realized **0.905 over days 0-39 (IN band of 0.850) but 0.963 over days 20-39 (OUT,
  +0.113)**; the second half works 693,672 s/day against the first half's 609,703, +14%;
- yet seconds per unit picked is **flat**: 100.41 over days 0-4, 99.19 over days 35-39 (fulfillment
  likewise, 18.52 -> 18.72). So this is not fragmentation and not travel drift -- ADR-0003's own-bin
  share of 0.000 says the same thing from the other side;
- `missed_share` peaks near day 30 and then falls: the window's first half reads 0.288 and its
  second half 0.170 (trend -0.118), while across all 40 days it RISES 0.109 -> 0.229 (+0.120). A
  quantity that rises for thirty days and then falls is a transient the window is sitting inside.

The level fails on both leaves too (ff 0.118, store 0.229, against 0.078 expected), but a level
cannot be judged from inside a transient, so it rides the same question rather than its own.
-> [Fit the store's window to its own steady state](27-fit-the-store-window-to-its-steady-state.md).

Worth recording for whoever takes 27: the store's implied coverage puts **every** store SKU at
Q = 1 on the line floor running base stock (memory `coverage-in-days-floors-the-store-section`),
so each pick empties a bin and each replenishment opens a fresh one -- 77,544 put events for
249,640 units. Whether 40 days can ever be a steady state for a shelf shaped like that is the
question, not an assumption to carry in.

### The verdict the ticket asked for

**The era's numbers on both sections remain PROVISIONAL**, so
[Sequence the inbound funnel](05-sequence-the-inbound-funnel.md)'s hold on phase 1 stands and the
three open tickets on the inbound-optimization map stay gated. But the ledger has changed
character: 17 failed on two structural defects in the warehouse (under-fielding and no-top-up
put-away) and this run fails on one mis-apportioned constant and one window that is too short.
Neither failure touches the shelf, the floor, the fielding or the rework budget, and all four of
those now read exactly as designed.

Assets outside `.scratch`: the run's `run_spec.json` (staffing + coverage record) and the four
leaf sim DBs under `comparison_20260908_075736` carry every figure above.
