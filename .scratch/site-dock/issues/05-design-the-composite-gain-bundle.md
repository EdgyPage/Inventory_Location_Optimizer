# Design the composite gain bundle

Type: prototype
Status: resolved
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

## Answer

**A `SiteGainBundle` holding two whole `GainBundle`s, dispatched by a one-line owner cursor set
inside `_params` — because `place_load` already groups by BinKey and BinKey already determines
regime.** The charter's "per-unit, keyed by owning channel" needs no per-unit loop: the seam is
there, one level coarser, and free.

Prototype: [`../assets/prototype_site_gain_bundle.py`](../assets/prototype_site_gain_bundle.py) —
drives the REAL `_Evaluator` / `GainBundle` / `place_load` / `plan_order` over a mixed trailer with
store on `tmin` (merge adapter) and fulfillment on `fifo` (uniform adapter), so the composite must
swap the CODE PATH mid-trailer, not merely the data. Throwaway; delete freely.

### The finding that dissolved sub-question 1

**BinKey determines regime.** `binkey_of` (`inventory_common.py:314`) returns
`(handling, category, storage_size, unit_category)`, and `regime_of` (`regime.py:22`) reads exactly
those fields — `inventory_common.py:42-43` states it outright: *"regime is itself a BinKey
component"*. `place_load` (`gain.py:391-411`) already groups a load's units by BinKey and dispatches
per group. So the owner lookup is `BinKey -> regime`, a pure function of a key the loop already
holds.

### 1. The shape: a composite of two whole bundles

The prototype builds both candidates and they are **indistinguishable at the evaluator** — same
mixed-trailer cost (276.60), same dispatch trace, same `plan_order` ordering. So the choice is not
about behaviour. It is about where per-arm validation lives:

* **(A) composite of two `GainBundle`s** — `GainBundle.__init__`'s cross-field refusals survive for
  free. The prototype shows it rejecting `uniform=True` + `pool_factory` (*"the uniform adapter
  serves a family with NO pool"*).
* **(B) one bundle, owner-keyed dict fields** — the same contradiction is **accepted silently**,
  because the five arm fields no longer sit on one object to be checked against each other. B also
  rebuilds a facade object per group.

**A.** Its real payoff is sub-question 2: `_gain_bundle_for` is **completely unchanged**, called
twice, once per leaf, with that leaf's own `mgr` / `strat` / `sctx`. Faithful-to-arm is preserved
**structurally** rather than by argument — neither half is rebuilt, reinterpreted or averaged, so
each stays faithful by construction and the composite adds nothing neither arm would do. B would
re-implement the validation over dicts, or lose it.

**Half the bundle is already site-wide today** and needs no keying at all: `put_speed` comes from
`put_crew_spec()` (`workunits.py:571`) — one site CONFIG, no channel variation; `wp_of` is
`_wp_for(wp, unit)`, which **already** dispatches per regime through `wp.by_regime`; `binkey_of`
and `tier_ranks_for` are pure; `fee_threshold_days` / `urgency_horizon_days` are site CONFIG. Only
the five arm fields and the three aisle dicts are per-leaf.

### 2. The seam: a cursor in `_params`, not a wrapper around `place_load`

**The prototype killed its own first draft, and this is the load-bearing part.** Wrapping
`place_load`'s group loop from outside is the obvious shape and it is **wrong**: the real loop
shares ONE `avail_cache` across a load's groups *"so spill into an already-touched tier continues
where consumption left off"* (`gain.py:355-360`). Wrapping gives each group its own cache and
silently breaks that continuity for two same-regime groups spilling into a shared tier.

So: `self._key = own_key` inside `_params` — which `place_load` calls exactly once per group,
before it branches on the adapter — and `b` becomes a property resolving through the site bundle.
One line, no signature changes, the real loop and the real `alloc` untouched.

**Safe because a spill chain never crosses regimes**: `_chain` (`gain.py:227`) varies only `size`,
holding `handling` / `category` / `cat` fixed, and regime lives in those three. Every other
evaluator cache — `_sorted_now`, `_sorted_pred`, `_wp`, `_chain_cache`, `_worst`, `_mom`, `taken` —
is BinKey- or bin-id-keyed, so **one evaluator serves both leaves with zero cross-talk**. This is
pinned in the docstring, because it is the property the whole design rests on.

**Rejected: explicit threading** (passing the resolved bundle down through `_place_merge` /
`_place_uniform` / `_place_pool` / `_tier_sorted` / `_avail` / `_unseated_cost`). It widens six
private signatures to carry a value that is a pure function of a key those functions already
receive — a shallow seam, and the hidden-state objection it answers is bounded anyway: the cursor
is set and read inside one group iteration that never yields.

### 3. One path, via a one-owner wrapper

`_Evaluator` always resolves through `for_key`; a single-channel run wraps its one `GainBundle` in
a trivial adapter that ignores the key and returns **that same instance**. Byte-identity
(CLAUDE.md section 2) holds because it is literally the same object, and there is no `if coupled:`
branch in the pricing hot path to rot. **Rejected: `_Evaluator` sniffing for a `for_key`
attribute** — two paths wearing one name.

This half is byte-identical today and goes in first and alone, graduated as
[Seat the one-owner bundle indirection](13-seat-the-one-owner-bundle-indirection.md).

### 4. Commensurability, and why the test is well-posed

**A mixed trailer decomposes EXACTLY by owner.** The prototype: store-only 124.28 +
fulfillment-only 152.32 = mixed 276.60, to the last bit. Because BinKeys partition bins by regime,
the two owners **never contend** — there is no bin both halves could want. That exactness is the
precondition; without it the exchange rate below is not a well-defined quantity at all.

Solving `score = a*store_hours + b*ful_hours` recovers **a = b = 1.000000**: a plain sum, no
per-channel coefficient. The test **fails the moment anyone weights a channel**, because then
`a != b`.

Note what commensurability does **not** claim: the two regimes carry genuinely different pick costs
(`wp.by_regime` — different `pick_intercept`, `pick_per_item`), and they should. The claim is that
both are seconds of the **shared** cost model scalarized with one divisor, not two.

**The test, in three parts** (`Tests/unit/`, floats with tolerance, never `==`):

1. the decomposition is exact within tolerance — the precondition;
2. the recovered rate `a == b == 1` within tolerance;
3. **a sabotage**: inject a per-channel weight and assert the test FAILS.

Part 3 is not optional. Parts 1 and 2 both pass trivially today, and memory
`real-test-coverage-is-317` is the standing reason to refuse a test with no demonstration that it
can fail.

**The denominator guard** (memory `a-count-is-not-a-claim`): the trailer score is a *gain* — a
difference of two sums over the SAME load — so it is already denominated and a bigger trailer does
not win by being bigger. But any **report** of it must carry the unit count; the prototype shows a
12-unit trailer at 436.04 outscoring an 8-unit one at 276.60 while being *worse* per unit
(36.34 vs 34.57).

### 5. Two sub-questions routed, not resolved here

Neither is a scope cut — both stay on the route, on the ticket that owns them.

* **Sub-question 4 (the space view) goes to [Design the site space view](08-design-the-site-space-view.md).**
  It is 08's whole subject, and 08 already says the bundle reads whatever shape it lands on.
  Deciding it here would settle 08 without its four sub-questions in view. **The fact 08 is waiting
  on, established:** `SpaceView.empties` and `.predicted` are keyed by **BinKey** and `emptied_at`
  by `id(bin)` — all three **key-disjoint across regimes**, so composing them is a trivial union,
  not a merge. What is genuinely non-additive is `released_at`, `versions`, `frozen_at` and
  `window`. Posted to 08.
* **Sub-question 5 (the rider's bundles) goes to [Re-shape the funnel for arm pairs](06-reshape-the-funnel-for-arm-pairs.md).**
  The composite needs **no new check**: `_gain_bundle_for` is called twice and already refuses per
  arm, so an unfaithful half refuses at worker startup exactly as today. What is genuinely open is
  the SELECTION side — `run_restock_selection.py:188` computes `needs_bundle_extension` per rule
  per channel independently (`select()` builds `channels[channel]` separately), and a **diagonal
  pair** is runnable only if BOTH members are in `FAITHFUL_GAIN_FAMILIES`. That is pairing
  arithmetic, which is 06's. Posted to 06.
