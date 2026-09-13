# Extend the gain bundles

Type: task
Status: resolved
Blocked by: 08

## Question

Extend `_gain_bundle_for` so the gain policies can run on the placement families phase 1
actually selects. **Scope is unknown until phase 1's ranking exists** — this ticket is
blocked by 08 on paper, but genuinely gated on the phase-1 run, and must not start before
that ranking is recorded in the selection artifact.

The one piece that is NOT gated — the mandatory `fifo` rider, which blocks phase 2 outright —
was split out as
[Give the fifo rider a faithful gain bundle](21-give-fifo-a-faithful-gain-bundle.md) so it
does not sit unreachable behind this ticket's wait. It is also OUTSIDE 08's cap of three: the
cap governs which OPTIONAL families are worth extending, and the rider has no opt-out.

The gain evaluator today serves exactly four restock families — `tmin`/`tmax` (the proven
k-cheapest merge), `rank_popularity` (its own pool rebuilt over aisle-state copies) and
`rank_random` (the pool with a deterministic stand-in selector under expectation pricing).
Every other family raises loudly by design, and the raise's own docstring anticipated this
ticket: *"phase 2's top-k should extend this map consciously, not silently."*

Constraints from 08's resolution:

- **The cap is THREE new families**, decided before phase 1 ran so the campaign stays
  costable. If phase 1's top five holds more than three unfaithful families, take the three
  highest-ranked and backfill from the faithful set — do not quietly widen the cap.
- **Faithful-to-arm is the whole contract.** An evaluator that cannot rebuild the arm's own
  pool over copies would price a fiction under that arm's name. The `rank_popularity`
  precedent is the pattern: call the arm's OWN builder over the copies, so its selector
  closes over the copied `aisle_demand_sum` exactly as the production pool closes over the
  live one — a future tiebreak change then cannot leave the evaluator pricing a stale
  policy under the arm's name.
- **The zoning refusal stays.** A gain policy under velocity zoning still raises: the
  virtual pool ignores the band filter, so its gains would price bins the arm cannot grant.
  Phase 2 runs zoning off, so this is not in the way — do not weaken it to make a family fit.
- 06's Tier-1 cross-checks (equivalence + sabotage) apply to every family added, as they did
  for the families that landed with 14.

Likely candidates, from what tends to rank well on labor: the `rank_labor` family,
`cluster_map`/`cluster_map_rank`, and the `map`/`map_rank` pair. Two cautions if the map
family is drawn: its exact-LAP gate admits a small share of BinKey classes and a very small
share of assigned UNITS, so greedy earns nearly all of a Map-family result at scale — an
evaluator that models the exact solver faithfully would be modelling the rare path. And
`cluster_map_rank` is already the top `t_reord` cost in the suite, so extending it has a
runtime consequence beyond the build.

## Scope, now known (2026-09-13)

**Phase 1 has RUN (`comparison_20260913_113512`, 68 leaf units, 0 failures) and
`restock_selection.json` is written, so this ticket is UNBLOCKED and its scope is exactly
three families.**

`_gain_bundle_for` must serve, site-wide (the cap is the UNION across channels, because an
extension is work per FAMILY):

    rank_minlabor, rank_labor, rank_cartlabor

That is 08's extension cap of 3 exactly consumed. The selector backfilled PAST `comp` and
`cmin` -- both of which also need extending -- taking `tmin` (store rank 5) and `rank_random`
(store rank 7) instead, so the cap did the job it was written for.

Already faithful, nothing owed: `fifo`, `tmin`, `tmax`, `rank_popularity`, `rank_random`.

**Four of the six phase-2 rule pairs cannot run until this lands**, because a gain cell prices
EVERY arm in its set, so one unfaithful member refuses the whole unit at worker startup:

| rule pair (store x fulfillment) | unfaithful member(s) |
|---|---|
| `rank_cartlabor` x `rank_minlabor` | both |
| `rank_minlabor` x `tmin` | `rank_minlabor` |
| `rank_labor` x `rank_labor` | `rank_labor` |
| `tmin` x `rank_cartlabor` | `rank_cartlabor` |

The two that already run are `rank_random` x `rank_popularity` and the mandatory `fifo` rider
(whose faithful bundle ticket 21 built).

**Do NOT copy `rule_pairs.chosen` into `PHASE2_PAIRS` before this lands.** `validate_spec`
refuses a gain cell over an unfaithful rule -- correctly, and
`test_a_gain_cell_over_an_unfaithful_rule_is_refused` pins it -- so copying first puts a spec
in the tree that refuses at build. The order is: this ticket, THEN the copy (both
`rule_pairs.chosen` and `staffing.pin`), then phase 2.

`staffing.pin` for the copy, when the time comes:
`{'mixed_20260816_131535__mixed_realistic_bell_lt0': '0ed2dd1582af'}`.

**One caveat to carry into the extension, from the ranking itself:** the store top three are
separated by 0.005% and 0.12% (432.93 / 432.95 / 433.46 h) and fulfillment's top EIGHT span
0.73%. Which of those is "rank 1" is noise at this resolution, and the pairing is rank-aligned,
so the specific diagonal above is one of several equally defensible draws rather than a derived
optimum. It does not change WHICH families need extending -- all three are in whatever the
order -- and it does not threaten the campaign, whose real signal is the 6.1% (store) / 8.0%
(fulfillment) gap from the best rule down to the order-blind `fifo` control. It is a caveat the
campaign publishes rather than discovers.

## Answer

**DONE.** `_gain_bundle_for` serves `rank_minlabor`, `rank_labor` and `rank_cartlabor`, and
all three are in `Inbound.gain.FAITHFUL_GAIN_FAMILIES` — the two edits the ticket names,
both made, so the selector cannot backfill past a family the driver runs and the driver
cannot admit a family the selector has not counted. 08's cap of three is exactly consumed,
not widened; the zoning refusal is untouched; `comp` and `cmin` stay unfaithful, as the
selector's own backfill decided. All six of phase 2's rule pairs now build.

### What the extension actually was

Not a fourth branch in a dispatch chain. The three families are pool-shaped like
`rank_popularity`, so their FIDELITY story is the one ticket 14 already told — call the
arm's own builder over copies. What is new is WHICH copies. Their `take` commits to live
aisle bookkeeping the older families never touch:

| family | beyond the ranked three, `take` commits to |
|---|---|
| `rank_labor` | `_aisle_pick_load_sum` (the LPT balance's running total) |
| `rank_cartlabor` | `_aisle_pick_load_sum` + `_aisle_vol_sum` (the cart's mass) |
| `rank_minlabor` | `_aisle_member_pos` — `[aid][sku_idx].append(x_phys)`, two levels down |

The old seam could not express that: `GainBundle` carried three NAMED dicts and `_make_pool`
copied exactly those three, so serving three more families would have meant six named slots
and an eight-argument factory that five of eight families ignore — the next family making it
nine. So the seam was widened by one concept instead of by three dicts:

- `GainBundle.aisle_state` — `{manager attribute: the LIVE dict}` — replaces the three named
  slots, and `pool_factory(candidates, state, wp)` replaces the five-argument form. The
  names are the seam's whole vocabulary, so a future family is a driver branch plus a name.
- `Inbound.gain.AISLE_COPIERS` — `{name: how a copy of it is made}` — states the purity rule
  once per dict rather than once per arm, because what "a copy" IS belongs to the dict and
  not to the arm reading it. Two families sharing a dict share its copier.
- Both loud: a name with no copier is refused at bundle construction (worker startup), and
  `aisle_state` declared on an adapter that opens no pool is refused as the wiring error it
  is. The bundle holds the LIVE dicts; the copy is taken per evaluation, at `_make_pool`.

The third shape is why this is worth the paragraph. `_aisle_member_pos` is a dict of dicts of
LISTS, and `dict(d)` over it is a copy that shares its inner lists — the virtual placement
then appends a column position to the real warehouse, with no error and no symptom, and every
later placement in the run is priced against a warehouse that never happened. A per-dict
copier table makes that shape a declaration; a per-arm copy site makes it something each
branch has to remember.

### Two values hoisted out of the per-evaluation path (and why production changed)

`_make_pool` rebuilds the policy for EVERY virtual placement, and `plan_order` is O(yard^2)
of those per drain. So anything the arm's builder computes once per ARM is otherwise
recomputed thousands of times per drain. Two such values, both now optional keywords that
every production call leaves at `None` — inert by default, so no production path moves:

- `build_ranked_cartlabor_pool_fn(total_freq=...)`. Its own docstring already forbade moving
  `sum(freq_by_sku.values())` into the pool ("a dict-order change silently repricing every
  cart penalty"); calling the builder per evaluation would have done exactly that, AND paid
  an O(catalogue) sum per pool open. The driver sums once per arm and hands it down.
- `build_ranked_labor_pool_fn(geo_memos=...)` / `_build_travel_balanced_pool_fn`. The
  geometry memo is bin geometry, which no copy of the aisle state can move, so one dict for
  the arm is the same value by the same argument that makes it safe across opens today.
  Without it every candidate bin re-derives `location`/`x_phys`/`y_phys`/`height_multiplier`
  on every evaluation.

`rank_minlabor` needed neither: `_MinLaborPool` has no memo and no arm-level sum, so its
builder is called per evaluation exactly as production calls it per open.

### What proves it

`Tests/unit/test_gain_bundle_labor_families.py` (new, 17 tests), per family:

1. **Faithful-to-arm**: the pool the bundle opens over copies makes the same placements, and
   advances the same state, as the arm's PRODUCTION pool (the `Placement` its own `build`
   hook installs) from identical state — `==` on the floats, because the claim is
   byte-identity, not agreement.
2. **Purity over all six dicts** after a real `plan_order`, not just the three the older
   families touch.
3. **The sabotage** (06 Tier-1): with that dict's copier replaced by the identity, the same
   run MUST move the live dict — so the purity assertion cannot pass vacuously on a family
   that never committed to it.
4. The bundle holds the LIVE dicts (a copy there would freeze the warehouse at worker
   startup and price every later drain against it), and
5. the two hoists are one dict / one sum across evaluations.

The file is MUTATION-CHECKED rather than assumed (memory `real-test-coverage-is-317`): five
deliberate defects — `total_freq` not the arm's sum, the geo memo rebuilt per evaluation,
`beta` dropped from minlabor, `aisle_member_pos` copied shallowly, `aisle_pick_load_sum`
dropped from `rank_labor`'s state list — and **all five fail the file**. The first fixture
did NOT catch the `beta` mutation, which is how the affinity lifts came to be large: at this
cost scale a reward of ~2 never flips an argmin, so `rank_minlabor` placed identically with
its compaction term switched off and the equivalence was an agreement about a pure minimiser.

Suite: `Tests/unit` **2440 passed, 1 skipped**. `context/verify_context.py`,
`path_guard --scan` and `docref_guard --scan` all clean. Four existing tests stated the old
contract and were updated rather than deleted: the pool-adapter helper in `test_gain_plan`,
the refusal fixture in `test_restock_selection` (now `rank_maxlabor` — the worst-case control
whose MIRROR `rank_minlabor` is now served, so the gate is a gate and not a family prefix), a
docstring example in `test_funnel_window`, and the `_Order` stub, which had no
`expected_labor` — the field all three families sort on.

### Two things worth carrying forward

- **`beta` is inert for the travel-balanced family.** `build_ranked_labor_fn` /
  `build_ranked_labor_pool_fn` accept it and do not pass it on — `_travel_balanced_impl` has
  no affinity reward. That is production behaviour, mirrored exactly here; it is recorded
  because the signature invites the opposite assumption. Only `rank_minlabor` responds to it.
- **The runtime consequence the ticket warned about is real but bounded.** With both hoists
  in place, a travel-balanced pool open costs about what `_MinLaborPool` and
  `_RankedAssignPool` already cost the evaluator per open — i.e. the extension does not put
  these families in a different cost class from the two pool families phase 2 already sweeps.
  What it does not do is remove the O(yard^2)-opens-per-drain shape, which is the evaluator's
  and is measured by 31.

### What it unblocks

All six rule pairs, and with them the phase-2 launch. The next act is the COPY, which is now
its own ticket: [Copy the chosen rule pairs into phase 2](35-copy-the-chosen-pairs-into-phase-two.md)
— `PHASE2_PAIRS` and `PHASE2_STAFFING_PIN`, both `None` today and both refused rather than
defaulted. Then phase 2, then publish.

## Comments

2026-08-31, from resolving [Build the run-shape layer](18-build-the-run-shape-layer.md): the
gain-bundle gate is on the POLICY, not the arm, which surfaced a blocker that became its own
ticket — see [21](21-give-fifo-a-faithful-gain-bundle.md), which carries the full chain and the
faithfulness question. Do 21 first; it is unblocked and phase 2 cannot start without it.

2026-09-01: **21 is RESOLVED** — phase 2 is no longer blocked, and this ticket is the only one
left on the map. Two things it changed for the work here:

- **There are now THREE adapters, not two.** A family with no pool is not automatically
  unservable: `fifo` is priced by the exact expectation of its own draw
  (`Inbound.gain._place_uniform`). When a phase-1 pick has a per-unit assignment function with
  no ranked wave, ask whether its draw has a closed form before reaching for a pool.
- **`place_load` gained an `alloc` keyword** (the sweep's shared block allocator, one per greedy
  round). It is read by the uniform adapter only, so a new pool- or merge-shaped family can
  ignore it — but a new adapter whose takes are identity-blind must use it, or `plan_order`'s
  leftover union collapses to one load's worth.

The faithful set is now a NAMED constant, `Inbound.gain.FAITHFUL_GAIN_FAMILIES`, and this ticket
extends it rather than only extending `_gain_bundle_for`'s dispatch chain. It exists because
the phase-1 selector has to know which chosen rules need extending without importing the
simulation; `Tests/unit/test_restock_selection.py::test_the_faithful_set_is_the_one_the_driver_
actually_accepts` pins the constant against the branches the driver really has, so adding a
family to one without the other fails.

Two consequences for the work here:

- **The cap is enforced upstream now, not remembered.** `run_restock_selection` applies
  `--extension-cap` (default 3) while choosing, backfills from the faithful set, and records
  `backfilled_past` in the artifact. So this ticket's scope arrives as a LIST in
  `restock_selection.json` (`channels.<ch>.needs_bundle_extension`), per channel — and the two
  channels may legitimately hand over different families.
- **Adding a family is now two edits, both required**: the branch in `_gain_bundle_for` AND the
  name in `FAITHFUL_GAIN_FAMILIES`. A branch without the name would leave the selector
  backfilling past a family it could actually run; a name without the branch would put a rule
  into phase 2's arm set that dies at its first drain.

2026-08-31, from resolving "Design the phased funnel" (08): a related unmeasured cost that
belongs to whoever runs phase 2 rather than to this build. 13 recorded the `'all'`
futuresight window as O(n²·|batch|), which is verified — `_futuresight_window` copies the
whole remaining script once per batch, n(n−1)/2 batch-copies over a run. But that is the
MINOR term: `_window_rates` re-aggregates the window once per ENTRY CALL, i.e. once per
DRAIN, so the real cost is the n² multiplied by drains per batch. If the pilot's bench shows
it biting, the legal fix is memoizing `_window_rates` keyed on `demand_v` (the window is
replaced wholesale by the injection that bumps it) — 06's "legal-keyed-not-built" case, not
new cache machinery.

2026-09-04: a session opened this ticket to work it and **stood down — the gate is unmet.**
Phase 1 has not run: no `restock_selection.json` exists under `COMPARISON_OUTPUT_DIR`, the
pilot's answer records no phase-1 ranking, and the successor map's ticket
[Sequence the inbound funnel](../../department-calibration/issues/05-sequence-the-inbound-funnel.md)
currently recommends HOLDING phase 1 until the calibrated era lands (a ranking taken now would
feed a phase 2 in a different era). Not claimed. This ticket's scope arrives as
`channels.<ch>.needs_bundle_extension` in the artifact and does not exist yet; do not start it
from the "likely candidates" list above. Next act on this map: none — the frontier stays empty
until that calibration ticket resolves and phase 1 runs.

2026-09-05: the successor map RESOLVED
[Sequence the inbound funnel](../../department-calibration/issues/05-sequence-the-inbound-funnel.md):
phase 1 is HELD until department-calibration's
[Take the reference run](../../department-calibration/issues/09-take-the-reference-run.md) resolves.
Two tickets now sit between that lift and phase 1 on this map —
[Verify the derived receiving crew under arrivals](23-verify-the-derived-receiving-crew.md) and
[Re-size the funnel in site days](24-resize-the-funnel-in-site-days.md). This ticket's gate is
unchanged: the ranking in `restock_selection.json`, which does not exist yet.

2026-09-10, from resolving
[Decide the contention regime under the derived crew](25-decide-the-contention-regime-under-the-derived-crew.md):
gate unchanged (the ranking in `restock_selection.json`), but note for whoever works it that
under the site-dock coupling a gain bundle prices a trailer against TWO arms at once (a store
rule and a fulfillment rule), so `channels.<ch>.needs_bundle_extension` names families per
channel and the bundle for a paired cell is the pair of them.

2026-09-13, from resolving [Gate the campaign axis on what a coupled run can do](33-gate-the-campaign-axis-on-the-coupled-model.md):
gate unchanged (still the ranking in `restock_selection.json`), but the consequence of SKIPPING
this ticket is no longer silent. `validate_spec` now refuses a spec whose `rule_pairs` name a
family outside `Inbound.gain.FAITHFUL_GAIN_FAMILIES` while any cell names a gain policy — so
copying the artifact's `rule_pairs.chosen` before extending `_gain_bundle_for` fails at spec
build, by name, instead of at the first drain of every gain cell after the freeze is paid for.

Two notes for whoever works this:

- **The refusal reads the live constant**, so it self-updates: extending the evaluator (the
  branch in `_gain_bundle_for` AND the name in `FAITHFUL_GAIN_FAMILIES`, both, as this ticket
  already says) is what clears it. There is nothing to edit in `whatif_config.py`.
- **The condition is "some cell names a `GAIN_POLICIES` entry"**, matching where
  `_gain_bundle_for` is actually called — a fifo/lifo-only matrix may sweep any rule, and a
  test pins that so the condition cannot quietly widen into "phase 2 may only ever sweep five
  families".
