# Derive the expected-travel closed form

Type: task
Status: open

AFK build with the derivation written up for review before anything lands (the map's execution
override: nothing committed without the user's go-ahead). Raised by the user after
[Take the reference run](09-take-the-reference-run.md), and it REPLACES the hybrid procedure of
[Choose the calibration procedure](02-choose-the-calibration-procedure.md): **there are no
calibration simulations.** Every equation is closed form and every input is known -- the
inventory's demand distribution, the warehouse geometry (known at simulation runtime, once the
warehouse is built), the travel speeds, the handling coefficients, the cart -- so seconds per
unit is an expectation over known distributions, computed AT SETUP from the run's own geometry,
and the equilibrium staffing balances around those expected values. Skills: `domain-modeling`
(*Reference run* and *Calibration record* leave the glossary or change meaning; *Knowability*
in the charter is amended), `codebase-design` (the derivation is one pure module every arm,
catalogue and geometry reuses).

## Question

Derive E[s_pick] per channel and E[s_put] as closed-form functions of the run's parameters and
compute them in the staffing derivation at setup, after the warehouse is built and the initial
placement is known, so a change of warehouse geometry changes the inputs and never the method.

**What the simulator charges, from the code (the derivation reproduces THESE rules, not a
textbook router):**

- Travel to a bin is rectilinear at two paces: `x_phys · x_pace + y_phys · y_pace`, positions in
  inches, pace = `sec_per_inch(speed_ft_per_sec)` (`Warehouse/kernel/cost_model.py`:
  `sec_per_inch`, `travel_cost`).
- A task is one aisle visit: `Task.from_batch` groups a batch's lines by aisle and
  `_plan_aisle_path` orders the bins (`Warehouse/picking/Workload_Builder.py`). Entry is the
  first bin-to-bin segment; a one-way lane pays an EXIT to the far end and descent to the ground
  (`aisle_exit_cost`); a two-way lane pays none.
- Cart next-fit: a pick that does not fit the cart's remaining volume triggers a swap
  (`cart_step`) -- a travel term that depends on the line-volume distribution and the cart.
- Handling per pick is `M(y) · (intercept + qty · per_item + qty · var(weight, volume))` with
  `height_multiplier` brackets (`per_pick`, `handle_var`, `height_multiplier`); this half is
  ALREADY closed form -- `simconfig/staffing.analytic_pick` prices the script line by line and
  `script_totals` sums it. The new work is the travel half beside it.
- Put-away: each put is costed from the aisle mouth, travel plus the same handling expression,
  with NO travel between placements (`Warehouse/operations/putaway.py`: `put_cost`). E[s_put]
  is therefore the placement-weighted mean of `x · x_pace + y · y_pace` over destination bins
  plus the handling term -- the simplest case; do it first.
- Batch content is a lift-weighted sample over SKUs from demand weights and affinity lifts
  (`_lift_weighted_sample_v2`, the v2 Fenwick sampler) at a line fraction the derivation sets
  (`batch_content`). This is the distribution the expectations are taken over.

**The expectation to build.** For a day of n lines drawn from the SKU distribution p, mapped by
the placement to a distribution over bins and hence over aisles:

- expected distinct bins visited Σ_b (1 − (1 − p_b)^n) and expected aisles visited over the
  aisle marginals -- the classical sums, exact for i.i.d. draws;
- expected within-aisle path length given k picks in an aisle of length L under
  `_plan_aisle_path`'s ordering, plus the one-way exit, plus the vertical term from the bin
  height distribution;
- expected cart swaps per task from the line-volume distribution and the cart capacity;
- expected task seconds ÷ expected units -- the same ratio `s_pick` was defined as (Σ task
  duration ÷ Σ items).

The fixed point is then one scalar equation per channel, s = h + t(n(s)) with
n(s) = K · S · ρ / (s · units-per-line), solved by a root-finder at setup in milliseconds. The
daily demand, batch content, put crew and receiving crew derive from it exactly as today.

**Geometry at runtime.** The derivation reads the BUILT warehouse (aisle count, lengths, lane
direction, bin positions and heights, section membership per channel) and the placement the
run just made, not constants. `Warehouse/layout/Aisle_Dimensions.py` (`uniform_aisle_bins`,
`catalog_aisle_bins`) is where the geometry is authored; the derivation consumes what was
built. Placement differs per arm, so E[s_pick] is per arm by construction; the era's declared
input stays the picker count per channel.

**What goes away.** The calibration record's `measured` provenance, the travel shares, the
reference run, the pass cap and the window precondition as a calibration step. The record
becomes the derived expectation stamped `derived` (the provenance enum already has it), with
the geometry and placement fingerprints it was computed from. The equilibrium check
(`simconfig/equilibrium.py`) stays as the REPORT it also is on every run -- it is how a
formula error would show, as an out-of-band day, not as a calibration input.

**Development-time correctness check, not a pipeline step.** 09's six passes trace t(n) on
both channels under `fifo` (store 793 -> 556 lines/day: 86 -> 107 s/unit; fulfillment
14,982 -> 1,597 lines/day: 21.5 -> 29.3), with per-task `task_stats` (duration,
`num_bins_visited`, `total_items`) and `work_events` put spans in the run roots named on 09.
Use them once to confirm the formula reproduces the simulator's own charges within a stated
tolerance, record the residual and its cause (LPT tails, stockout-thinned lists), and then the
formula is the calibration. No new runs are taken for it.

**Also fix the denomination in the inventory.** Stock coverage is authored as generation-time
batches (`equilibrium_coverage_batches`), which is the bespoke conversion that put the first
reorder wave past day 40 on 09. Coverage per SKU in days of its own expected demand carries no
implicit batch, and the expected reorder flow and its first-cycle timing are then closed form
too (first reorder at (Q − r)/d days, identical for every popularity shape). Decide here
whether that lands as a generator knob on the profile or as a runtime rescaling at setup.

The answer records the equations with their assumptions, the module they live in, the
development-time residual against the six passes, the amended provenance of the staffing
record, and the glossary/charter amendments. Done when: gates green, the flag-off path
byte-identical, the derived expectations stamped on `staffing.derived[<pair>]` with geometry
and placement fingerprints, and the era launchable with no calibration record at all.
