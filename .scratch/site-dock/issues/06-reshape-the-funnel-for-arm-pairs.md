# Re-shape the funnel for arm pairs

Type: grilling
Status: resolved
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

## Answer

**A phase-2 cell is an ordered list of RULE pairs; the diagonal extends to `stock_mode`, so a
rule pair is two arm pairs and never four; EVERY cell couples, `inb_off` included; the extension
cap counts the UNION of families; and the staffing pin is the pair's whole derived block.**

### 0. Four findings that shaped the rest

- **`sorted(set(arms))` destroys the thing the diagonal needs.** `_choose` returns its arm set
  sorted (`run_restock_selection.py:246`), and rank order is the ONLY thing a diagonal reads. The
  artifact as written cannot carry a pairing at all — this is not a shape preference, it is a
  missing field.
- **The `uni`/`opt` axis has a structural handle: `Strategy.stock_mode`** (`'uniform'` vs
  `'policy'`). It partitions cleanly — 17 arms each, exactly one per mode in every one of the 17
  rules — so "pair like with like" reads off the grid. No key-prefix parse, the same discipline
  `_rule_of` already enforces one level up (`:97-107`: `uni_rank_labor_norsl` splits into three
  fields whose middle one contains underscores, so every hand parse is a guess).
- **Nothing in the codebase reads two run roots.** Every analysis surface is cell- or run-scoped;
  the only cross-phase artifact is `restock_selection.json` and its consumer is a human copying
  into `whatif_config`. So there is no site at which a phase-1 number could be joined to a
  phase-2 one, and no runtime refusal can guard that join. Section 3 is a stamp, not a gate.
- **Work units are built PER CELL**, inside the cell loop after `_apply_cell` has written
  `CONFIG['global']['inbound_*']` (`scenario.py:166-183`). So "the coupling rides the inbound
  flag" is mechanically a per-cell property, free either way — which turns section 3 from a
  plumbing question into a genuine choice.

### 1. The selection hand-off: an ordered list of rule pairs

**`restock_selection.json` gains `pairs`: an ordered tuple of `(store_rule, fulfillment_rule)`,
rank-aligned, store first.** `_rank_channel` is unchanged — the two channels still rank
independently, which is right, because their hour scales are not comparable (their receiving loads
differ 7.4x, the same fact behind `PHASE2_THRESHOLD_DAYS`). The diagonal is applied AFTER ranking,
by zipping the two `chosen` lists — the ordered ones, not `arms`.

**Ragged rankings.** The two lists genuinely can differ in length: a rule disqualified for a
missing or non-finite reading, a cap that backfills on one side only, or `len(chosen) < k`. The
zip takes the common length, logs loudly, and the artifact carries `pairs_complete: false` with
the reason. `select()` still WRITES — the ranking is the valuable part and a human needs to read
it to decide whether to re-run phase 1 or accept a shorter campaign. The refusal that costs money
lives at the phase-2 launcher, not here.

**Rejected: two ordered lists the consumer zips.** It keeps raggedness representable all the way
to the launcher, and a zip that truncates silently is exactly the shrink-with-no-error
`_inbound_axis` already refuses three separate ways (`cells.py:120-143`).

### 2. `PHASE2_PAIRS`, and the refusal that gets stronger

**`PHASE2_ARMS` becomes `PHASE2_PAIRS`, an ordered tuple of rule pairs.** The rename is
load-bearing: a stale flat tuple left in the file must fail at spec build rather than be read as
a pair list. `CHANNEL_RESTOCKS` is then DERIVED, not authored — `{p[0] for p in pairs}` for store,
`{p[1] for p in pairs}` for fulfillment — which is what each leaf needs to build its strategy
list, and which cannot drift from the pairing because it is computed from it.

**The refusal keeps its job and gains reach.** `_run_whatif_matrix` (`scenario.py:96-125`) still
refuses an inbound matrix with no arm set; it now checks SHAPE — pairs present, every entry a
2-tuple of known rules, and `('fifo', 'fifo')` among them. That last clause is strictly stronger
than today's `'fifo' not in arms`: it catches `fifo` on one side only, which a flat tuple cannot
express and therefore cannot detect. The rider is appended as a pair if absent, outside k and
outside the cap, exactly as the scalar rider is today.

**Unit arithmetic.** `len(pairs)` rule pairs x 2 stock modes = `2 * len(pairs)` coupled units per
(cell, inventory pair) — the same arm count as today's per-channel sweep, now fielded as half as
many units each doing both leaves. Each store arm appears in exactly one unit and each fulfillment
arm in exactly one unit: a bijection, because a rule cannot occupy two ranks.

**A naming trap, recorded because it will bite at build time.** `pair` ALREADY means the inventory
profile throughout the harness — `_build_work_units(pairs, ...)`, `<cell>/<pair>/`,
`_derive_staffing_for_pair`, `restock_selection.json`'s `run.pairs`. An arm pair is a different
thing at a different level. Every identifier and every comment says **arm pair** or **rule pair**,
never a bare `pair`. Not added to `CONTEXT.md`: this is harness vocabulary, in the same family
03 explicitly declined to add (leaf, work unit, channel run, coupled run).

### 3. Every cell couples, including `inb_off` — and the caveat is a stamp

**Coupling is a property of the RUN, not of the cell: all ten cells run the coupled model.**

The trade is explicit. Under the leaf model `inb_off` was comparable with phase 1 but confounded
against its own nine siblings, which run at 1x site put and receiving capacity while it runs at 2x
(`workunits.py:366-369` hands EACH leaf the whole derived site crew; `staffing.py:710-712` says
outright those two crews are site totals). Coupling every cell inverts that: the matrix is
internally valid everywhere, and only the cross-phase comparison is lost. **The matrix's own deltas
are what the campaign publishes**, so that is the side to keep whole.

**The asymmetry is not a clean scaling, which is why "just note it" was not enough.** What doubles
is put and receiving CAPACITY, not labour seconds. Under drain-or-cap a larger crew cuts less and
completes more per day, so `ss_prod_total` (unload + put + pick) moves by an amount that differs
per arm — precisely by how much an arm trades put time for pick time, which is the trade phase 2
exists to measure. A rank comparison across the boundary is therefore no more invariant than an
hours comparison.

**How it is carried, given there is nothing to refuse:** `restock_selection.json` stamps
`put_regime: 'per-leaf'`; the phase-2 `run_layout.json` stamps `coupled: true` for the whole run
(03 section 5, unchanged — the run-root marker stays honest precisely BECAUSE no cell opts out);
and the caveat is published with the campaign, as the map already requires. There is no runtime
gate because there is no join to gate. The one reachable mistake is section 7's.

**`whatif_config.py:166-170` must be rewritten**, not merely amended: "the validity check that
phase 1's ranking transferred" and "comparable with phase 1" are both false under this answer, and
a stale comment claiming a cross-phase guarantee is worse than no comment.

### 4. The staffing pin is per SITE, and it is well-posed for a recordable reason

**Pin the whole `staffing.derived[<label>]` block, compared with `_staffing.derived_differs`.**

`_derive_staffing_for_pair` runs per pair, above the channel loop (`workunits.py:875`), and
`_record_derived` keys on `derived[label]` (`:705-726`) — one block per inventory pair holding the
two SITE crews plus per-channel `k_pickers`. There is no per-channel derivation to pin. A
per-channel pin would also be strictly weaker: it could pass on both channels' picker counts while
the site put or receiving crew had moved underneath, which is the exact `calibration_stale` drift
the pin exists to catch. Reusing `derived_differs` rather than growing a second notion of "same
record" also inherits its shape-move diagnosis for free.

**Why it is well-posed at all, and worth recording:** coupling does not change the derivation —
only how the derived crews are FIELDED. The record is byte-identical across phases, so the pin
works unchanged across the coupling boundary. That is the same fact that makes 02's double count a
DELETION rather than a re-derivation.

### 5. The per-cell overrides: confirmed, nothing changes

The ten entries cover the union of all eleven keys and `inb_off` restates every one of them, so
`_inbound_axis`'s partial-entry refusal (`cells.py:135-143`) is satisfied as written. The axis
writes `CONFIG['global']`, which is already site-scoped — the yard and the dock were never
per-channel — so coupling touches the axis not at all, and `inb_off` now switches off ONE site
pipeline because there only ever was one in CONFIG.

**One build note, not a decision:** `inbound_spec()` is read inside `_prepare_channel_run`, so
under coupling it must be resolved ONCE per unit rather than once per leaf, with the two leaves
refusing to differ. It belongs with 02's `_prepare_site_run` composition.

### 6. The anchor keeps its cell, restated

`inb_off` stays, as the **inbound-OFF pole inside the coupled model**: the control for "does
running an inbound pipeline at all change the answer, versus which policy runs it", and the only
cell where no yard exists — which makes it the structural zero for every quantity
[Define the yard metrics](../../inbound-optimization/issues/07-define-the-yard-metrics.md)
declares. A control that shares its site model with everything it is compared against is worth
more than one that shares it with a run nobody publishes.

### 7. The one guard that is reachable

**`select()` refuses a coupled run root.** Phase 1 is uncoupled by definition, so a coupled root is
not a phase-1 run; pointed at one, the selector would rank coupled leaves happily and emit a
hand-off artifact indistinguishable from a real one. It already loads `read_run_layout(base_dir)`
at `:283` for the spec and schema id, so the marker 03 put there is one `if` away. This is the only
place in the entire hand-off where a wrong-phase number can enter the campaign silently.

### 8. The extension cap counts the union of families

**The cap becomes a bound on the UNION of distinct unfaithful families across both channels**,
with channels consumed in the artifact's existing deterministic order (`sorted(by_channel.items())`
gives fulfillment, then store) and that order RECORDED in the artifact as part of the decision.

Today the cap is applied per channel (`:221-229`), so a cap of 3 can commit the project to six
extensions. But extending `_gain_bundle_for` is work per FAMILY, site-wide: both channels choosing
`map` costs one extension, not two. A bound that can be silently doubled is not bounding the thing
it was created to bound, and the cap's whole purpose is keeping phase 2 costable.

**The order-dependence is real and is the accepted cost.** One channel's choice now depends on the
other's via an alphabetical order that means nothing physically. There is no order-free
alternative: the two channels' rankings share units but not scales, so no global rank exists to
allocate against. Declared and recorded beats arbitrary and hidden.

**And an arm pair is runnable only if BOTH members are faithful**, so `needs_bundle_extension` is
reported per pair as well as per rule — the property the rider check needs, since a gain cell
builds a bundle for every arm in the set and refuses at worker startup for any arm it cannot serve
(`Inbound/gain.py:126`).
