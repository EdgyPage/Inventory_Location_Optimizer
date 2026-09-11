# Re-shape the funnel for arm pairs

Type: grilling
Status: open
Blocked by: 02

HITL. Skills: `grilling`. Blocked by
[Design the coupled work unit](02-design-the-coupled-work-unit.md): the cell arithmetic follows the
unit's shape.

## Question

The charter makes a phase-2 cell a **pair of arms, diagonal by rank**, plus the `fifo`/`fifo` pair
as reference. Work that through the funnel's three surfaces.

1. **The selection hand-off.** `run_restock_selection.py:139-158` (`_rank_channel`), `:280-298`
   (`by_channel`), `:337-346` ranks per channel independently and pastes each channel's `arms` into
   `PHASE2_ARMS`. Decide what the hand-off carries under diagonal pairing (an ordered pair list?
   two ranked lists the consumer zips?), and what happens when the two channels' rankings are
   different lengths or one channel's cap binds and the other's does not.

2. **`PHASE2_ARMS` and the refusal.** `whatif_config.py:60`, `:217` is one flat arm tuple for both
   channels, and `_run_whatif_matrix` REFUSES an inbound matrix with no arm set rather than falling
   through to the committed full suite. Redefine the spec shape and keep the refusal — it is the
   thing standing between a typo and a 34×10 run that is not the funnel at all.

3. **Phase 1 stays uncoupled, and says so.** Phase 1 is inbound-off, so by the charter it keeps the
   leaf model and ranks per channel. That leaves the asymmetry the map names: phase 1 ranks under
   2× the site put labour, phase 2 runs under 1×. Decide how that caveat is CARRIED — a stamp on
   `restock_selection.json`, a line in the published caveats, or a refusal if anyone tries to
   compare a phase-1 number to a phase-2 one.

4. **The staffing pin.** `restock_selection.json` was to pin the staffing record and
   `inbound_policies` refuse under a different one. With pairs, decide whether the pin is per
   channel or per site (the map's fog names this).

5. **The per-cell overrides.** `phase2_inbound_axis` (`whatif_config.py:137-184`) applies CONFIG
   overrides identically to both channels and the `inb_off` anchor (`:181-183`) now switches off
   **one** site pipeline rather than two. Confirm the ten entries still state every key they touch —
   `_apply_cell` mutates a process-wide CONFIG that is never reset between cells, and `_inbound_axis`
   refuses a partial entry for exactly that reason.

Do NOT size the campaign here: that is inbound-optimization
[Re-size the funnel in site days](../../inbound-optimization/issues/24-resize-the-funnel-in-site-days.md),
which is blocked on this ticket answering what a cell IS.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§3.

## Comments

**From [Design the composite gain bundle](05-design-the-composite-gain-bundle.md) (resolved):** its
sub-question 5 (the rider's bundles) is routed here, because what is open about it turned out to be
pairing arithmetic rather than evaluator design.

* **The composite needs no new faithfulness check.** `_gain_bundle_for` is called twice under the
  coupled unit — once per leaf, unchanged — so an arm outside `FAITHFUL_GAIN_FAMILIES`
  (`Inbound/gain.py:126`) refuses at worker startup exactly as it does today, on whichever side it
  sits. Nothing is added and nothing is weakened.
* **What is open is the SELECTION side.** `run_restock_selection.py:188` computes
  `needs_bundle_extension` per rule, and `select()` builds `channels[channel]` **independently per
  channel** (`:298`). Under diagonal pairing a pair is runnable only if **both** members are
  faithful, so `needs_bundle_extension` becomes a property of a PAIR, not of a rule in a channel.
  The extension cap (`:221-229`) is counted per channel today and would need the same re-reading.
* The extension itself stays where it is: inbound-optimization
  [Extend the gain bundles](../../inbound-optimization/issues/20-extend-the-gain-bundles.md), gated
  on phase 1.

This interacts with the map's own fog entry *"Where the campaign's staffing record pins a PAIR"* —
both are the same re-shaping question seen from two sides.
