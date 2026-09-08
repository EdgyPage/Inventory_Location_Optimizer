# Close the put closed form's three known gaps

Type: task
Status: open

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
