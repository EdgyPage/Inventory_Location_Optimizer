# Design the composite gain bundle

Type: prototype
Status: open
Blocked by: 01

HITL. Skills: `prototype` + `grilling`. Blocked by
[Design the site receiving coordinator](01-design-the-site-receiving-coordinator.md): the bundle is
consumed inside the drain, and pack ownership is settled there.

## Question

The sizing inventory calls this the sharpest open design question in the whole effort.
`_gain_bundle_for` (`strategy_runner.py:200-267`) reaches **one** `mgr`'s placement machinery —
`_aisle_sku_sets` / `_aisle_idx_sets` / `_aisle_demand_sum` — one `wp`, one `strat.restock`, one
`put_speed`, one zoning check (`:215`). `GainBundle` (`Inbound/gain.py:133-186`) and
`_place_merge` / `_place_pool` / `_place_uniform` (`:415`, `:469`, `:558`) each assume **one pool
per tier**. A mixed trailer's store units must be priced by the store arm's pool and its
fulfillment units by the fulfillment arm's.

The charter fixes the answer's shape — **per-unit bundle keyed by owning channel, summed to one
trailer score in hours** — and leaves the build open:

1. **The lookup.** A composite bundle holding two `GainBundle`s and dispatching per unit, or one
   bundle whose pools are keyed by owner? Prototype both cheaply enough to react to; the existing
   [`prototype_gain_evaluator.py`](../../inbound-optimization/assets/prototype_gain_evaluator.py)
   is the precedent for what "cheap" means here.

2. **Faithful-to-arm, twice.** The evaluator is FAITHFUL-TO-ARM by
   inbound-optimization decision 10 — it prices against the arm's own machinery, not a model of it.
   With two arms, confirm each half stays faithful to *its* arm and that the composite adds nothing
   neither arm would do.

3. **Commensurability, tested not assumed.** The charter asserts a fulfillment hour and a store
   hour are worth the same to the site, because the objective is put + pick hours from the shared
   cost model with no per-channel weighting. Design the test that would FAIL if that stopped being
   true — the map's Notes commit to testing it, and memory `a-count-is-not-a-claim` is the reason:
   a summed score with no denominator cannot tell "this trailer is worth more" from "this trailer
   is bigger".

4. **The space view.** `strategy_runner.py:1093-1094` attaches `_SpaceTimeline(_drain_sku)` to
   **one** manager's placement (`Inbound/space.py:118`, `:156`). A site yard must see both leaves'
   free space; decide whether the timeline becomes site-scoped or composite, and keep memory
   `free-bins-counts-the-whole-geometry` in view — a leaf's free count already includes the other
   channel's section, which is exactly the confusion a site view must not inherit.

5. **The rider's bundles.** `FAITHFUL_GAIN_FAMILIES` (`Inbound/gain.py:126`) gates which restock
   families the evaluator can serve. Under diagonal pairing, **both** members of a pair must be
   covered — `run_restock_selection.py:188`, `:252-253` (`needs_bundle_extension`) currently checks
   one. Note the dependency, but the extension itself is inbound-optimization
   [Extend the gain bundles](../../inbound-optimization/issues/20-extend-the-gain-bundles.md),
   still gated on phase 1.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§3, last row.
