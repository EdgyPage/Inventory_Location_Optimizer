# Close the put closed form's three known gaps

Type: task
Status: resolved

Graduated 2026-09-08 from [Give put-away a per-channel expected travel](26-give-put-away-a-per-channel-price.md),
whose adversarial verification measured all three while checking that the per-channel price was
sound. AFK build. Skills: `codebase-design`.

## Question

The put-away expectation is now charged PER CHANNEL and lands well inside the band on both leaves
(store 0.371 expected against 0.362 realized, fulfillment 0.475 against 0.477). That is good enough
to draw a band around, and it hid three separable fidelity gaps that 26 measured and deliberately
did not fold in -- a band is now drawn around this number, so its residuals stop being harmless.

**Gap 1 -- the reorder lot is rounded, so units-per-pack is biased high.**
`Optimization/simconfig/staffing.py` `implied_reorders` prices ONE pack plan at a ROUNDED lot and
scales it by a FRACTIONAL reorder count:

    lot = reorder_lot(c)
    reorders = units / lot                       # an expectation: kept fractional
    plan = _receive(c, max(1, int(round(lot))))   # the packer packs whole units

Every pack-proportional term -- the whole travel term and the put intercept -- is therefore
under-counted. Measured on `comparison_20260908_075736` against the run's own `work_events`:

| | closed form | realized | error |
|---|---|---|---|
| store travel | 15.966 s/unit | 17.043 | **-6.32%** |
| store handling | 82.892 | 81.459 | **+1.76%** |
| store TOTAL | 98.858 | 98.502 | +0.36% |
| ff travel | 24.298 | 25.044 | -2.98% |
| ff handling | 4.034 | 4.107 | -1.77% |
| ff TOTAL | 28.332 | 29.151 | **-2.81%** |

units per pack: +6.99% store, +3.05% fulfillment. Packs/day derived vs realized: 1,852.9 vs 1,938.6
store, 13,637.8 vs 13,722.0 fulfillment. **The store's apparent +0.4% accuracy is two errors
cancelling** (travel -6.3% against handling +1.8%) and is an accident of this catalogue's mix: the
store's put load is mostly handling, fulfillment's is 86% travel, so the same bias lands almost 1:1
on the fulfillment leaf. Do not read the store's agreement as validation. The same bias is on the
RECEIVING side by construction, since `packs` comes from the same plan.

**Gap 2 -- there is no cart-swap term at all.** `_cost_putaway` charges `q.spec.swap_coef` per
next-fit cart swap; `implied_reorders` has no swap term (contrast picking, which has
`expected_travel.expected_swaps`). It is exactly zero today because `PUT_QUEUE_SPLIT = False` gives
one uncarted queue and `PUT_SWAP_COEF = 0.0` -- an exact re-pricing of the run confirms zero swap
seconds in `work_events` -- so this is a silent under-price waiting for the first
`--put-swap-coef > 0` run. Related: `--put-queue-split` with per-queue crew speeds is not modelled
either (`_bind_put_crews` honours a queue crew's own speed; the closed form uses one site
`put_speed`).

**Gap 3 -- an unbuilt class is priced at ZERO travel, silently.**
`Optimization/simconfig/expected_travel.py` `Geometry.class_mean_travel` returns `0.0` and
`class_mean_height_mult` returns `1.0` when a BinKey has no aisles (`return tot / n if n else 0.0`).
A reorder pack whose class the planner never built is therefore free, with no warning. It did not
fire on this run (all 48 store / 3 fulfillment classes resolved), but the stage-A distribution is
built from `viable_storage_units` and nothing guarantees the planner emitted every class it names.

## Done when

- The lot is kept as an expectation through the packer, or the pack count is priced against the
  SKU's stamped line law rather than one rounded plan, and BOTH channels' closed forms come within
  ~1% of the realized per-unit put cost on the reference pair. The receiving side is re-read at the
  same time, since it shares the pack count.
- The swap term is either modelled or the era derivation REFUSES a configuration it cannot price
  (a carted put queue, a nonzero swap coefficient) rather than under-pricing it silently. A refusal
  is an acceptable answer here and is cheaper than a model nobody will exercise.
- An unbuilt BinKey raises or warns rather than pricing at zero.
- The correction's effect on the two expected utilizations is stated. The store leaf is expected to
  move very little (its two errors currently cancel) and fulfillment's expectation to rise from
  0.475 toward ~0.489; both stay well inside `band_tol`, so this is a fidelity fix and not a
  band rescue -- say so plainly if the numbers agree.

## Answer

RESOLVED 2026-09-08. All three gaps closed in one commit; verified by re-pricing the check
run's own script offline against its `work_events` (`comparison_20260908_094846`, fifo, 40 era
days) -- no new run, and the harness reproduced the run's recorded derivation to the digit first
(packs 74,117.04, put seconds 25,236,659.4).

**Gap 1 -- the lots are the script's, line by line.** The formula was right; the lot
distribution it was priced over was not the sim's. `staffing.reorder_lot` (one lot, rounded,
scaled by a fractional reorder count) is retired for two pure functions:

- `fired_lots(order, quantities)`: the lots `_fire_reorders` fires while a SKU's script lines
  are picked, in script order. Under base stock the shelf holds Q at every day's start, a line
  serves min(q, Q), fires exactly what it took and re-offers the rest (nothing is lost, 27), so a
  line is `q // Q` lots of Q plus one of `q % Q` -- the reference pair fires 1.3 lots per line.
  With a pipeline a lot below P + 1 is priced as P + 1 at a unit-conserving count (a floor,
  exact at P = 0, every catalogue today). Above the floor: the position at the reorder point,
  `Q + P - rp`, reorders `Σq ÷ lot` kept FRACTIONAL -- a steady-state expectation, because a
  40-day script fires nothing for a SKU whose demand never crosses Q - rp (no fielded catalogue
  has such a SKU; the crossing line's overshoot, about half a mean line, is not modelled).
- `received_law(lot, cv)`: what arrives -- the ledger's `max(1, round(N(lot, lot·cv)))` on unit
  cells (the catalogue's supply_cv runs to 0.15, so 6.5% of the store's arrivals exceed Q); a
  fractional lot with no jitter is the mixture of its two integer neighbours, so the packer is
  asked two whole questions instead of one rounded one.
- `implied_reorders` prices each (SKU, arrived quantity) once through the sim's own packer and
  caches it; `ScriptTotals` carries `per_sku_lines` (script order, the lots need it) instead of
  `per_sku_units`, and a `lots` total that the record now stamps beside `packs`. 2.9 s / 11 s per
  channel on the reference pair.

THE MEASUREMENT (closed form vs realized, same script):

| | before | after |
|---|---|---|
| store units per pack | +6.99% | **-0.38%** |
| store put s/unit | +0.36% (cancelling) | +1.81% |
| store recv s/pack | +8.69% | +1.50% |
| ff units per pack | +3.05% | **+0.12%** |
| ff put s/unit | -2.81% | **-0.14%** |
| ff recv s/pack | +1.16% | +0.05% |

The store's remaining +1.8% is NOT the model: it is uniform across every per-unit term
including receiving, which is exact per pack, and packs per unit read +0.38% -- so it is the SKU
MIX of the window. The run put away 249,640 of the script's 255,817 units; the 9,763 still
standing at the end of day 40 (day-40 lots and pending re-offers) carry a mean handling term of
91.0 against the script's 63.6. Weighting each SKU's closed-form price by the units the run
actually put away: **put +0.21% (store) / -0.16% (ff), receiving +0.01% / -0.09%, packs per unit
+0.16% / -0.15%**. Both channels are inside the ~1% the ticket asked for, on the receiving side
too.

**Gap 2 -- refused, not modelled.** The single queue the era runs (`put_queue.single_queue`)
never reads `swap_coef` at all -- the coefficient only reaches `store_and_fulfillment(...)` under
the split -- so a `--put-swap-coef` typed with the era would have been recorded and ignored.
`_check_era_flags` now refuses a nonzero coefficient beside the split it already refused, and
`workunits.refuse_unpriceable_put(CONFIG['global'])` refuses both at the derivation seam for a
caller that reached it without the parser. No swap model: cheaper than one nobody exercises.

**Gap 3 -- an unbuilt class raises.** `expected_travel.UnbuiltClass` from `Geometry.class_aisles`
on both `put_site_pricer` branches (the `initial` fallback too) and on the pick side's uniform
`accumulate`, which carried handling at M = 1 with no routing rate for the same reason. The
reference pair resolves every class (48 store / 3 fulfillment, re-checked by the harness).

**Effect on the record** (Done-when 4): store `s_put` 98.858 -> 100.285, fulfillment 28.332 ->
29.111; put load 1,437,958 -> 1,472,246 s/day, put crew **59 -> 61**, receiving load 533,428 ->
537,811 s/day, receiving crew 22 unchanged. Against the run's own crew of 59 the expected put
utilization becomes store 0.378 (realized 0.362; was 0.371) and fulfillment 0.489 (realized 0.477;
was 0.475) -- the ticket's predicted 0.489 exactly; under the derived 61 they read 0.365 / 0.473.
Every band stays well inside `band_tol`: a fidelity fix, not a band rescue. A resume of a
pre-change era run re-derives a different put crew and raises, as 03 intended; analysis warns
and stamps.

Tests: `fired_lots` in both regimes, `received_law` (sums to one, the central cell by hand, the
floor at one), a lot-by-lot sabotage test against the retired rounded rule and the jitter
reaching the packer, `UnbuiltClass` on every reader, the swap refusal at the CLI and the seam.
The derivation note gained §8 (`assets/expected-travel-derivation.md`). 218 unit tests across the
touched files green, preflight's two canaries end to end, all other gates green; the
architecture layer re-syncs in the following chore commit.
