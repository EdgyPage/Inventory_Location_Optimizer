"""site_space — one space view for the site, composed from one frozen view per leaf.

`SpaceTimeline` stays ONE PER LEAF and `freeze(mgr, epoch)` keeps its single-manager
signature and its purity pin.  All the site-ness lives here, in a pure function over
already-frozen data: `compose_site_view` takes what each leaf froze and returns the one
view a drain's decisions read.

# ── why this is not in `space.py` ─────────────────────────────────────────────────

`Inbound/space.py` states its own defining property in its docstring — *"This module
imports nothing from Warehouse at all"* — and the composer needs `regime_of` to check
which channel a bin belongs to.  So the rule gets its own module rather than costing
`space.py` the property that makes it testable in isolation.  `regime_of` is `wh_kernel`
and imports nothing itself, so `inbound -> wh_kernel` is the only edge this adds.

# ── the deletion test ─────────────────────────────────────────────────────────────

Delete this and every gain `@ordering` entry has to learn to read TWO views, at five
`.get(key, ())` sites each.  Its interface is one function over frozen data — the
smallest surface that carries the whole rule.

# ── the one that would have been silent ───────────────────────────────────────────

**A leaf's `empties` carries the OTHER channel's bins.**  It is snapshotted from
`mgr._index`, which is the WHOLE geometry's free index, and a leaf simulates only its own
section — so every leaf's view lists the other channel's free bins as permanently,
falsely available (memory `free-bins-counts-the-whole-geometry`; `CONTEXT.md` states it
under **Free index**).  A naive union of two leaves' views therefore imports BOTH leaves'
phantoms, and a mixed trailer's store units would rank against fulfillment bins that no
store putter can ever reach.  So `empties` is FILTERED per contributing leaf and the other
two tiers are not: `predicted` is projected from that leaf's own demand over its own SKUs,
and `emptied_at` is harvested from that leaf's own reclaims, so both are already leaf-own.
Filtering all three uniformly would be three times the surface for one real defect.

The invariant that establishes, and it is the point of the whole module: **the composed
key set is PARTITIONED BY REGIME**, so a mixed trailer's store units rank against store
bins and its fulfillment units against fulfillment bins.  There is no site free-space
*scalar* anywhere in this design, and there never was one that meant anything.

# ── what a composition of ONE is ──────────────────────────────────────────────────

The view itself, by identity.  A composition of one partitions nothing, so there is
nothing to filter — and filtering it would silently change every standing-yard run on the
books, removing a phantom that belongs to the uncoupled model rather than to a defect this
function is allowed to fix on its way past.
"""
from __future__ import annotations

from Warehouse.kernel.regime import regime_of

from Inbound.space import SpaceView


def compose_site_view(contributions) -> SpaceView | None:
    """One site view from `[(regime, view), ...]`, one entry per leaf that froze.

    `regime` is the CONTRIBUTING LEAF's channel — the coordinator tags each contribution
    because it is the thing that holds both managers and called `freeze` on each, which is
    the same knowledge its `{sku: leaf}` owner dict is built from.  It is allowed to be
    None on a single-entry composition and only there: nothing is partitioned, so there is
    nothing for a tag to decide.

    Returns None when nothing froze (no leaf runs a timeline), which is every run without
    a standing yard, and the single view UNCHANGED when one leaf froze.
    """
    entries = [(r, v) for r, v in contributions if v is not None]
    if not entries:
        return None
    if len(entries) == 1:
        # BY IDENTITY, tag or no tag.  See the module docstring: a composition of one is
        # the thing itself, and filtering here would move every standing-yard run.
        return entries[0][1]

    tagless = [i for i, (r, _) in enumerate(entries) if r is None]
    if tagless:
        raise ValueError(
            f'contribution(s) {tagless} carry no regime tag, and {len(entries)} leaves are '
            f'being composed; the tag is what partitions `empties`, and an untagged '
            f'contribution would put one channel\'s free bins in front of the other '
            f'channel\'s units')
    regimes = [r for r, _ in entries]
    if len(set(regimes)) != len(regimes):
        raise ValueError(
            f'two contributions claim the same regime {regimes!r}; a site has one leaf per '
            f'channel, and two would each filter to the same bins and then collide on '
            f'every key')

    # ONE SITE EPOCH.  The coordinator drains once, so both freezes take the same instant
    # and the ambiguity dissolves rather than being resolved.  Checked, because a composed
    # view stamped with one leaf's epoch while carrying the other's bins is a view whose
    # urgency gate reads a "now" that never happened.
    stamps = {v.frozen_at for _, v in entries}
    if len(stamps) != 1:
        raise ValueError(
            f'the leaves froze at different instants {sorted(stamps)}; one drain is one '
            f'freeze, and `frozen_at` is the urgency gate\'s "now"')

    # THE FUTURESIGHT WINDOW IS REFUSED, NOT ZIPPED.  The rule is settled and written down
    # below for whoever lifts this; what is refused is shipping it unexercised.  `_window`
    # is None on every LAWFUL arm (`futuresight` is the declared-unlawful entry), so a zip
    # here would be a path no run takes -- and an unexercised path is not a feature, it is
    # a place for one to rot (memory `hand-run-test-tiers-rot-silently`).
    #
    # The rule, when a lawful arm needs it: zip the two tuples BY BATCH INDEX (legitimate --
    # one batch is one site day, and the staffing derivation refuses channels with different
    # batch counts), then union each pair of `{sku: qty}` dicts, which are disjoint because
    # no SKU belongs to both leaves.
    windowed = [r for r, v in entries if v.window is not None]
    if windowed:
        raise ValueError(
            f'leaf/leaves {windowed} carry a futuresight window, and composing two windows '
            f'is a rule this function states but deliberately does not implement: no lawful '
            f'arm builds one, so the zip would ship unexercised. See the comment above this '
            f'refusal for the rule to build when an arm needs it')

    empties: dict = {}
    predicted: dict = {}
    emptied_at: dict = {}
    for regime, view in entries:
        for key, bins in view.empties.items():
            if not bins:
                continue
            # THE FILTER, AND THE TRAP UNDER IT.  The decision is by the leaf's TAG; the
            # bin is what CHECKS it.  Never `regime_of(key)`: `BinKey` is a PLAIN TUPLE,
            # so every `getattr` in `regime_of` falls through and it returns 'store' for a
            # fulfillment key -- silently, and always in the same direction.  Verified.
            if regime_of(bins[0]) != regime:
                continue
            if regime_of(bins[-1]) != regime:
                raise ValueError(
                    f'key {key!r} holds bins of more than one regime; a BinKey is derived '
                    f'from the bin\'s own four attributes, so this cannot happen unless the '
                    f'index has been built by hand')
            if key in empties:
                # UNREACHABLE WHILE THE FILTER IS RIGHT, and said so rather than tested:
                # each contribution keeps only its own regime's keys and no two
                # contributions share a regime (refused above), so a collision means the
                # filter let a key of the other's regime through.  Defence in depth, not a
                # branch a test can drive.
                raise ValueError(
                    f'two leaves both contributed free bins under {key!r}; the filter is '
                    f'what makes the key set a PARTITION, so a collision means one leaf '
                    f'kept a key of the other\'s regime')
            # THE SAME TUPLE OBJECT, not a rebuild: the frozen view is pinned read-only BY
            # IDENTITY downstream (`view.empties[key] is empties_before`), and a rebuilt
            # tuple passes every equality test while breaking that pin.
            empties[key] = bins
        for key, bins in view.predicted.items():
            if key in predicted:
                raise ValueError(
                    f'two leaves both predicted a clear under {key!r}; `predicted` is '
                    f'projected from a leaf\'s own demand over its own SKUs, so the key '
                    f'sets are disjoint by construction')
            predicted[key] = bins
        # `emptied_at` IS KEYED BY `id(bin)`, AND THE LEAVES HOLD TWO WAREHOUSES.  Each
        # leaf builds its own `Warehouse_Builder().from_config(...).build()`, so the two
        # id-spaces are independent and CPython recycles ids freely -- a collision here
        # would hand a lookup for one leaf's bin the other leaf's stamp, for a different
        # bin, with nothing on the value saying so.  Nothing reads this field in
        # production today, which is exactly why it would have been got wrong quietly.
        for bin_id, stamp in view.emptied_at.items():
            if bin_id in emptied_at:
                raise ValueError(
                    f'two leaves stamped a clear for bin id {bin_id}; the leaves hold '
                    f'separate warehouses, so this is an id collision between two '
                    f'different bins and not one bin emptied twice')
            emptied_at[bin_id] = stamp

    return SpaceView(
        empties=empties,
        emptied_at=emptied_at,
        predicted=predicted,
        # PER REGIME, NEVER AVERAGED -- and note the TYPE changes here: a leaf's view
        # carries a float, a composed view carries `{regime: float | None}`.  The release
        # instant is a FACT (the release of THAT channel's batch), and the two leaves
        # legitimately release at different instants inside one site day; one batch is one
        # site day, it is not one site MOMENT.  An averaged stamp would be a fabricated
        # time on a view whose whole charter is that the only times it carries are facts.
        # Nothing in production reads this, which is why the split costs nothing.
        released_at={r: v.released_at for r, v in entries},
        # ELEMENT-WISE, and a SUM is the one illegal form.  Slot 0 still means "demand
        # changed", slot 1 "the free index changed", slot 2 "a bin filled", so a future
        # sub-vector cache key still works and no fourth counter appears.  Summing is
        # lossy in precisely the failing direction: leaf A +1 / leaf B +0 and A +0 / B +1
        # sum identically, which is the "two version-equal freezes straddle a real change"
        # bug the eviction fold was written to kill.  Magnitudes are meaningless by
        # contract; equality is the only operation.
        versions=tuple(tuple(v.versions[slot] for _, v in entries) for slot in range(3)),
        frozen_at=next(iter(stamps)),
        window=None)
