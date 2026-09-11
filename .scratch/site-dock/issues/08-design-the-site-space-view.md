# Design the site space view

Type: grilling
Status: resolved
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

## Answer

**The coordinator composes two frozen views; `freeze` is not touched.** `Inbound/space.py` keeps
its defining property — *"This module imports nothing from Warehouse at all"* (`space.py:20-23`) —
`freeze(mgr, epoch)` keeps its single-manager signature and its purity pin, and the site-ness lives
entirely in a composer the coordinator calls.

**And the ticket's field census was nearly inverted.** Only **four** `SpaceView` fields are read in
production; of the "genuinely non-additive four" the ticket inherited, two are read by nothing at
all.

### 0. What actually reads a view

The four gain `@ordering` entries are the only production consumers (`gain.py:712-760`). The
seeded `fifo`/`lifo` keys take `ctx` and never touch it — the parameter is unused
(`priorities.py:82-110`) — and `Inbound/trailer.py` contains no reference to a view at all.

| field | production readers | kind |
|---|---|---|
| `empties` | `gain.py:314,333,349,457,570` — always `.get(key, ())` | per-BinKey dict |
| `predicted` | the same five sites, gated on the `predicted` flag | per-BinKey dict |
| `frozen_at` | `gain.py:734`, the urgency gate's "now" | scalar |
| `window` | `gain.py:752` | tuple of `{sku: qty}` dicts |
| `emptied_at`, `released_at`, `versions` | **none** — asserted only in tests | — |

That census is what makes this ticket cheap. It is recorded here because the next reader will
otherwise re-derive it from the dataclass, which lists seven fields as if they were seven problems.

### 1. The shape: composition, not a wider signature

**`SpaceTimeline` stays one per leaf; the coordinator composes.** Every hook (`harvest`, `evict`,
`fill`), every counter, `attach`'s one-attribute rebind and the purity pin all stay exactly as they
are, and the composer is a new pure function taking frozen views and returning one.

**Rejected: a multi-manager `freeze`.** It widens the one interface the purity pin is written
against (`test_space_timeline.py:267-276`), and `attach(mgr)` still binds one manager, so the
class would be half site-scoped and half not.

**Rejected: one timeline attached to both managers.** Genuinely tempting — one counter vector,
site-complete, no pair to justify. But `attach` rebinds a single attribute on a single manager (the
`BinRecorder` precedent) and `freeze` still reads `mgr._index` / `mgr._sku_*_bins` off **one**
manager, so this fixes the counters and leaves the actual freeze argument exactly where it was.
It buys the easy half of the problem.

The composer passes the **deletion test**: delete it and every gain entry has to learn to read two
views, at five `.get(key, ())` sites each. Its interface is one function over frozen data — the
smallest surface that carries the whole rule.

### 2. Site free space: `empties` is filtered, the other two are already clean

The two tiers are built differently, and only one of them is contaminated:

```python
empties = {key: tuple(bins) for key, bins in mgr._index.items() if bins}
```

`_index` is the **whole geometry's** free index (`Inventory_Management.py:194`), and a leaf
simulates only its own section — memory `free-bins-counts-the-whole-geometry`, and `CONTEXT.md`
already states the rule under **Free index**: *"A whole-geometry count that includes another
channel's section is not a section's free index."* So each leaf's `empties` carries the other
channel's bins as permanently, falsely free. **A naive union imports both leaves' phantoms.**

`predicted` is different: it is projected from `self.demand` over `mgr._sku_singleton_bins` /
`_sku_pallet_bins` (`space.py:227-237`), and a leaf's demand covers only its own SKUs — so it is
already leaf-own. `emptied_at` is harvested from that leaf's own reclaims.

**So: filter `empties`, union `predicted` and `emptied_at` unchanged.** Filtering all three
uniformly would look tidier and would be three times the surface for one real defect.

**The invariant this establishes, and it is the point of the whole ticket:** the composed key set
is **partitioned by regime**, so a mixed trailer's store units rank against store bins and its
fulfillment units against fulfillment bins. There is no site free-space *scalar* anywhere in this
design, and there never was one that meant anything.

**Identity survives composition.** `test_gain_plan.py:748` pins the frozen view read-only *by
identity* — `assert view.empties[_KEY_M] is empties_before`. Because every key comes from exactly
one leaf, the composer passes the **same tuple objects** through and that pin holds untouched. Do
not rebuild the tuples.

### 3. The four scalars

**`versions`: composed ELEMENT-WISE, `((d, d'), (r, r'), (f, f'))`.** Not a flat pair of triples.
The cache-sharing boundary (inbound-optimization 06) draws **sub-vector** keys — the predicted
projection keys on `demand_v`, the empties snapshot on `(reclaim_v, fill_v)` — and that only works
while the slots are positionally addressable *by event class*. Element-wise keeps slot 0 meaning
"demand changed", slot 1 "the free index changed", slot 2 "a bin filled", so 06's projection works
unchanged, equality remains the only operation, and **no fourth counter appears** — the module's
two explicit prohibitions (`space.py:47-50`) both hold.

**A SUM is the one illegal form.** Magnitudes are meaningless by contract, and summing is lossy in
precisely the failing direction: leaf A +1 / leaf B +0 and A +0 / B +1 sum identically. That is the
"two version-equal freezes straddle a real change" bug the eviction fold was written to kill
(`space.py:56-64`, `test_space_timeline.py:426-441`).

**Costs nothing today: no version-keyed cache exists anywhere in production.** Every cache in
`gain.py` is intra-call and BinKey- or bin-id-keyed (`gain.py:210-215`, `:386`). This decision is
entirely about not disarming the contract before the cache lands.

**`frozen_at`: one site epoch.** The coordinator drains once, so both freezes take the same
instant; the ambiguity dissolves rather than being resolved. The existing pins already assert
`frozen_at == epoch` (`test_space_timeline.py:324`, `:507`).

**`released_at`: per leaf, carried per regime, never averaged.** It is a fact — the release instant
of *that* channel's batch (`space.py:100-102`) — and the two leaves legitimately release at
different instants inside one site day. One batch is one site day; it is not one site *moment*. An
averaged stamp would be a fabricated time on a view whose entire charter is that the only times it
carries are facts. Nothing in production reads it, which is why this is cheap — and exactly why it
would otherwise have been got wrong quietly.

**`window`: the rule is a per-batch-index zip with a per-SKU union; the build REFUSES until a
lawful arm needs it.** The semantics are settled and recorded here: zip the two tuples by batch
index (legitimate — one batch is one site day, and `staffing.py:715-727` refuses channels with
different batch counts), then union each pair of `{sku: qty}` dicts, **disjoint by 01's own
assertion** that no SKU belongs to both leaves. But `_window` is `None` on **every lawful arm**
(`strategy_runner.py:1348-1352`; `futuresight` is the declared-unlawful entry), so the composer
asserts both windows are `None` and **refuses loudly otherwise** rather than shipping a zip no arm
exercises. The rule above is written down for whoever lifts the refusal. Memory
`hand-run-test-tiers-rot-silently` is why: an unexercised path is not a feature, it is a place for
one to rot.

### 4. Two injections, one per leaf

The batch streams are genuinely per channel, and `inject_demand` replaces wholesale
(`space.py:175-179`) — demand is one batch deep by charter, never an accumulation. **Each leaf
injects into its own timeline and bumps its own `demand_v`.** Merging the two would fuse two
scripts into one and make `demand_v` mean "either channel released", which the counters'
partition-by-what-changed rule rejects outright.

The rollover carry needs nothing new: `space.py` never sees a carry as a separate thing — the
driver merges `_pending` into the batch's items before calling (`strategy_runner.py:1342-1344`),
and that merged map is arithmetically the same one the pickers are handed
(`_eff_batch.items`, `:1485-1492`). Per leaf, that stays true unchanged.

### 5. How the regime filter resolves — and the trap under it

**`BinKey` is a PLAIN TUPLE** — `BinKey = tuple[str, str, str, str]`
(`inventory_common.py:311`), not a NamedTuple. So `regime_of(key)` falls through every `getattr`
and returns `'store'`. **Verified this session against both a store key and a fulfillment key:
both answer `'store'`, silently.** Nothing does this today — every site resolves regime from an
*entity*, and `inventory_common.py:42` says why out loud: a pool takes a representative unit
because *"regime is itself a BinKey component; passing one is how the pool says so out loud."*

So the filter does **not** ask the key:

- **The mechanism: the coordinator tags each contribution by the leaf it just froze.** It already
  knows — it holds both managers and calls `freeze` on each. Zero new coupling, and it mirrors
  01's `{sku: leaf}` dict.
- **The assertion: `regime_of(bin)` on the dict's VALUE.** Legal (`regime_of` is `wh_kernel`,
  dependency-free, and duck-types over Bins), and it is the check that catches the section-2
  phantom leak if the filter is ever wrong.
- **Never `regime_of(key)`**, and that line earns a comment where someone will reach for it,
  because it fails silently and always in the same direction.

`Inbound -> wh_inventory` is forbidden (`architecture.yml:100`), so `binkey_of` cannot be imported
here — but the fallback if leaf-tagging ever proves awkward is **injection, with a live
precedent**: `binkey_of` already crosses into `Inbound/` on the gain bundle (`gain.py:155,158,181`,
called at `:392`), exactly as `drain_sku` is injected into the timeline.

### 6. What proves it neutral

The existing pins do most of the work, and the new one is a composer test, not a run test:

- `freeze` is untouched, so `test_freeze_is_pure` (`:267`) still covers it verbatim — the
  ten-slot `_mgr_fingerprint` (bins compared by **identity**) and whole-state `random.getstate()`
  equality.
- The composer is a pure function over frozen data: test it directly on two hand-built views.
  Assert the regime partition, the **identity** pass-through of the tuples, element-wise versions,
  and the window refusal.
- Flag-off and `fifo`/`fifo` are already guarded end to end by
  `test_the_degenerate_lockstep_holds_with_the_timeline_on` (`:180`),
  `test_the_timeline_changes_nothing_on_the_standing_path_end_to_end` (`:245`) and the run-level
  `Tests/e2e/test_standing_yard_e2e.py:193`, which compares every table row-for-row. A coupled run
  builds no composer when the flag is off, so none of them move.

The trap, from `lockstep-tests-compare-aggregates-only`: assert the composed **key sets and their
tuples**, never a count of bins.

### Written in this session

- `CONTEXT.md` — **Space view** amended. It read *"The per-drain frozen snapshot of the space
  timeline"*; under a site dock one view is composed from **two** timelines, one per leaf, and its
  key set is regime-partitioned. **Space timeline**, **Standing demand**, **Predicted clear** and
  **Free index** all stay exactly as written — Free index in particular already states this
  ticket's section-2 rule, which is why the answer cites it rather than coining anything.

### What this hands onward

- **No new ticket.** Section 5's `regime_of(key)` trap is a rule plus a comment, not a seam to
  harden: it is unreachable today and the build that would first reach it is this one.
- **The build** joins the map's fog patch: the composer itself, the `empties` regime filter with
  its `regime_of(bin)` assertion, element-wise versions, the per-regime `released_at`, the window
  refusal, and the composer's own unit test. It needs two frozen views to exist, so it waits on the
  coupled unit builder like the rest.
- **[Design the composite gain bundle](05-design-the-composite-gain-bundle.md)** reads whatever
  this landed on, and it lands well: the bundle's one-owner cursor already dispatches per BinKey
  group, and this view's key set is regime-partitioned, so the two compose without either knowing
  about the other.
