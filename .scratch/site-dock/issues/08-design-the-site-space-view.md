# Design the site space view

Type: grilling
Status: open
Blocked by: 01

HITL. Skills: `grilling` + `codebase-design`. Blocked by
[Design the site receiving coordinator](01-design-the-site-receiving-coordinator.md): the
coordinator owns the ctx freeze this view is frozen into.

## Question

Graduated out of 01, which settled the coordinator but deliberately left this: it is a real design
cost, not a signature tweak.

`_receive_standing` (`inventory_reorder.py:740`) does
`ctx.space = self.space_timeline.freeze(self, epoch)` — one frozen `SpaceView` per drain, which
every yard and dock policy then ranks on (`ctx.space` is the named-view arrival point the priority
seams reserved). `SpaceTimeline` is documented as **"one per arm"** (`Inbound/space.py:118`),
attached to a single manager's placement (`SpaceTimeline.attach`, `Inbound/space.py:156`;
`strategy_runner.py:1096`).

Under one yard that is the per-leaf artefact this map exists to kill: a **mixed** trailer would be
ranked on **half** the site's free space, so a gain policy would score a trailer's fulfillment
units against store-only headroom, or the reverse.

1. **The shape.** Does `freeze` grow a multi-manager signature, or does the coordinator compose two
   independently-frozen views into one site view? The second keeps `freeze(mgr, epoch)` untouched
   and pure, but a composed view is only correct if every quantity it carries is additive across
   leaves — which `demand`, `emptied_at` and the three version counters (`demand_v`, `reclaim_v`,
   `fill_v`) are **not** obviously all of. Check each before choosing.

2. **What "site free space" even means here.** Memory `free-bins-counts-the-whole-geometry` is the
   trap: a leaf's `free_bins` already counts the **other** channel's section, so "both leaves'
   free space" may double-count today rather than under-count. Establish what the current number
   actually is before designing its site version — the answer may be that the existing per-leaf
   view is already site-scoped by accident, and the defect is the opposite of the one assumed.

3. **The purity pin.** `Tests/unit/test_space_timeline.py` pins `freeze` as pure with respect to
   the manager (mutates no manager state, consumes no RNG). Whatever this becomes must keep that
   pin, and the degenerate-lockstep neutrality argument with it: with both policies `fifo` the view
   must stay pure data, so a site view must not perturb a `fifo`/`fifo` run.

4. **The standing-demand injection.** `inject_demand` is driven per batch by the driver
   (`strategy_runner.py:1330`, before `check_reorders`) with the released batch plus the rollover
   carry — **per channel**, because the batch streams are per channel. Decide whether the site
   view takes two injections or one merged one, and what `released_at` means when the two leaves'
   batches are released at different instants.

Ticket 05 (the composite gain bundle) is the other consumer of this view; whatever shape this
lands on, the bundle reads it.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§1 (the `_SpaceTimeline` row). Re-resolve its line numbers before trusting one.
