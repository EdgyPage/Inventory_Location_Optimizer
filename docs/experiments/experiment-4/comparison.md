# {{ experiment().title }} — comparison

<!-- The run write-up. Fill the Summary/Discussion prose; the matrix table, scatter, figures and
     formulas resolve from experiment.yml + data/whatif_delta.json. -->

!!! note "Summary"
    Across the 9-cell matrix, **aisle-splitting is the throughput lever and velocity zoning is a net
    loss**, with total labor essentially flat. The winner is **`k2_l0_off`** (split every aisle in
    two, no capacity loss, no zoning): **≈ +11% store / ≈ +6% fulfillment** steady-state throughput
    vs the `k1_off` baseline, improving **every one of the 34 arms on both channels**. Velocity
    zoning (2- or 3-band ABC) *reduces* that gain wherever it is added and, applied alone, costs the
    store channel ≈ −11%. A 10% capacity loss gives back a little throughput for a slight labor
    saving. Labor stays within ±1% everywhere — this warehouse's labor is **cart-swap-dominated**,
    so geometry moves throughput, not labor.

## Setup

**Pick-time cost model** (per-channel calibrations — store `StoreCart`, fulfillment
`FulfillmentCart`; full model + all calibrations on the [Formula reference](formula-reference.md)):

{{ pick_time_formula((experiment().inventories.keys() | list) | first) }}

{{ pick_calibration_table((experiment().inventories.keys() | list) | first) }}

**Assignment functions.** Every cell runs the full 17-family × {uni, opt} suite (34 arms); the
three highlighted here are the store's biggest gainers under the split (full catalogue on the
[Formula reference](formula-reference.md)):

{{ assignment_formulas() }}

## The cell matrix

One frozen inventory + one batch stream is reshaped into a combinatorial grid of **aisle-split**
`k` (segments per aisle) × **capacity_loss** (bins dropped per cut, `k > 1` only) × **velocity
zoning** (`off`, 2-band, 3-band ABC). `k = 1` is the un-split layout, so its loss collapses to 0.
That yields **9 cells**; the un-split, un-zoned cell **`k1_off`** is the reference every other cell
is measured against.

{{ whatif_matrix() }}

<figure markdown>
  ![Labor saving vs throughput gain, one point per cell × channel × arm](images/{{ experiment().whatif.scatter }}){ width=820 }
  <figcaption>Each point is one arm-run: total-labor saving % (x, → better) vs throughput gain %
  (y, ↑ better) against <code>k1_off</code>. Colour = cell, marker = channel. The split cells sit in
  the upper band (throughput up, labor ≈ 0); the zoned cells pull left/down and scatter wider.</figcaption>
</figure>

## Reading the matrix

- **The split is the whole story (`k2_l0_off`).** Halving aisle length with no capacity loss lifts
  store throughput to a **+11.1%** median (best arms `map_rank` / `cmin` / `map` ≈ **+14%**) and
  fulfillment to **+5.9%** (best `expn` ≈ +7.7%). It improves **100% of arm-runs** on both channels,
  and `uni` vs `opt` initial layouts land within 0.1 pp — the gain is **geometric** (shorter aisles
  ⇒ less cross-aisle x-travel per visit), not something an assignment function has to earn.
- **Velocity zoning subtracts.** Zoning *alone* (`k1_abc2` / `k1_abc3`, no split) costs the store
  channel ≈ **−11%** throughput (worst arms −19%) for no labor benefit. Zoning *on top of* the split
  (`k2_l0_abc2/abc3`) drops the store gain from +11% to ≈ +1.6% and blows out the arm spread (store
  range widens to roughly −7% … +34%). Fulfillment is less sensitive but never prefers zoning.
- **Capacity loss is a mild trade.** `k2_l10_*` gives back a little throughput vs the 0-loss split
  (store +10.3% vs +11.1%) but flips fulfillment labor slightly *positive* (+0.5 … +0.7%) — the only
  place any lever buys measurable labor.
- **Labor barely moves.** Every cell's labor median sits within ±1%. Consistent with the travel
  rework's finding that fulfillment task labor is ~87% cart-swap: the layout knobs change how fast
  the batch clears (throughput / makespan), not the total picking work.

## Discussion

The one-way lane model makes **per-visit x-travel scale with aisle length**, so halving the aisle
(`k = 2`) halves the dominant travel term on every visit — a change no assignment function can
reproduce, since they only choose *which* bin within a fixed geometry. That is why the split lifts
every arm by a similar amount and why `uni`/`opt` converge: the win is in the walls, not the wave.

Velocity zoning was the opposite bet — concentrate hot SKUs into a small near zone so batches skip
the cold aisles. It loses here because the "like-with-like" band restriction removes placement
freedom the arms were exploiting, and because gathering hot SKUs into fewer aisles *lengthens* the
per-visit sweep in the hot band — trading the very x-travel the split was cutting. The two levers
work against each other, and the split wins.

The practical read: **shorten aisles, don't zone.** The open follow-ups are (a) whether an
intermediate `k = 3–4` keeps paying off or hits a cart-swap floor, and (b) whether a *labor* lever
exists at all for a cart-swap-dominated channel — nothing in this matrix moved it. See
[Full results](full-results.md) for the winner and baseline cells arm-by-arm.
