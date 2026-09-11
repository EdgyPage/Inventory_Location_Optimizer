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

## Comments

**From [Design the composite gain bundle](05-design-the-composite-gain-bundle.md) (resolved):** its
sub-question 4 was the space view, and it is routed here whole rather than pre-empted — 05 decided
only that it does not own this. Its own answer leans on nothing this ticket has not settled.

One fact for sub-question 1 ("check each before choosing"), established while resolving 05 and
verified against the source:

* **`empties`, `predicted` and `emptied_at` are key-disjoint across regimes.** `SpaceView.empties`
  and `.predicted` are `dict[BinKey, tuple[Bin, ...]]` (`Inbound/space.py:91`, `:96`), and BinKey
  **determines** regime — `binkey_of` returns `(handling, category, storage_size, unit_category)`
  and `regime_of` reads exactly those fields (`inventory_common.py:42-43`: *"regime is itself a
  BinKey component"*). So the two leaves' tier dicts share **no key at all**, and composing them is
  a trivial union, not an additive merge with a correctness argument to make. `emptied_at` is
  `dict[id(bin), float]` — disjoint for the same reason.
* **The genuinely non-additive four are `released_at`, `versions`, `frozen_at` and `window`.** That
  is where sub-question 1's real work is: three of them have no meaning under two leaves without a
  decision (`released_at` is sub-question 4's own question; `versions` is equality-only by contract,
  so a composed pair is not obviously a version at all).

This narrows the ticket but does not answer it: whether `freeze` grows a signature or the
coordinator composes two frozen views is still open, and so is what a composed `versions` means to
the cache layer that keys on it.

Also relevant: memory `free-bins-counts-the-whole-geometry` is confirmed as sub-question 2 suspects
— **each leaf builds the WHOLE geometry and simulates only its own section.** So two leaves'
free counts are over the same bins, and summing them double-counts the site. The disjointness above
is what makes the correct composition cheap anyway: take each leaf's own-regime keys.
