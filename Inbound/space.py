"""space — the event-stamped space timeline the standing dock's decisions read.

Two tiers against the absolute clock, and nothing inferred between them: bins that are
empty NOW (with the actual second each ran dry, where a pick produced one), and bins
PREDICTED to clear from the standing demand — the released-but-unpicked batch, one batch
deep, which is information a real WMS has.  Predictions are deliberately UNTIMED: no
makespan estimates, no pick rates against the clock.  The only times carried are facts —
actual clear stamps, and the release instant of the demand.  ("How far ahead can
availability reliably be planned" is a future view-arm family, parked in the
inbound-optimization map's fog.)

# ── how it attaches, and why flag-off is byte-identical by construction ───────────

`SpaceTimeline` is constructed by the DRIVER when `INBOUND_STANDING_YARD` is on — always
on with the flag, no policy gate, no extra knob — and attached to the manager instance
(`attach(mgr)`, the `BinRecorder.attach` rebind precedent: one attribute, no listener
registry).  Flag-off it is never constructed, and every manager-side hook is an
`is None` no-op, so the store-only path cannot be perturbed.

The drain rule is INJECTED at construction (`Warehouse.picking.Workload_Builder.
drain_sku`, handed over by the driver) because the import edge `Inbound -> wh_picking`
is forbidden — the broker holds what it is handed.  This module imports nothing from
Warehouse at all.

# ── the four touchpoints (decisions are drain-quantized; data is event-stamped) ───

    inject_demand   driver, BEFORE check_reorders: the batch about to be released plus
                    the rollover carry — everything released-but-unpicked at the
                    decision instant
    harvest         `_reclaim_empty_bins`: the actual `_emptied_at` stamps, captured at
                    the one moment the stamps and the bins meet before both are wiped
    fill            `_execute_placement`: a bin was occupied — version bump, stamp expiry
    freeze          ctx-freeze inside `_receive_standing`: ONE projection per drain
                    serves every decision in it (no per-decision rescans), delivered as
                    `ctx.space`, the named view the priority seams reserved

# ── the version contract (what the cache ticket keys on) ──────────────────────────

Three per-event-class counters, so tables can be processed in parallel without an I/O
race: `demand_v` +1 per injection (once per batch), `reclaim_v` +1 per harvested bin,
`fill_v` +1 per placement.  EQUALITY is the only legal operation — magnitudes and
cross-class comparisons are meaningless by contract.  The predicted set is a pure
function of the other three states, so it carries no fourth counter, and the cache
layer composes keys from this vector and may not add counters of its own.

One honest gap, stated for that cache layer: `requeue_bin` evictions (reloader arms)
return a bin to the free index through NONE of the three event classes, so two version-
equal freezes can straddle an eviction-only change.  The frozen views themselves are
always correct — they snapshot live state — but a cache keyed on this vector alone is
stale across an eviction until the evicted unit re-places (which bumps `fill_v`).
"""
from __future__ import annotations

from collections import defaultdict


class SpaceView:
    """One drain's frozen view of warehouse space — pure data, copied at freeze.

    Policy keys read it as `ctx.space` (None when no standing yard runs).  The staging
    decision anchors to `empties` — immediately available bins, the operationally stable
    signal; `predicted` rides as data for the planning formulation still being designed
    ("Define the inbound objective").  NO aggregates live here: what gets derived from
    the tiers is the evaluator's business.

    The container sets are FROZEN copies (the live `_index` lists swap-remove mid-drain,
    so a held reference would violate the frozen-ctx contract); the `Bin` objects inside
    are the live ones — a key that reads `bin.storage` mid-drain is reading stale-by-
    design data, exactly as the `put_policy` purity contract states.
    """

    __slots__ = ('empties', 'emptied_at', 'predicted', 'released_at', 'versions',
                 'frozen_at')

    def __init__(self, empties, emptied_at, predicted, released_at, versions,
                 frozen_at):
        #: dict[BinKey, tuple[Bin, ...]] — bins free NOW, per tier (snapshot of _index).
        self.empties = empties
        #: dict[id(bin), float] — the ACTUAL absolute second a bin ran dry.  Harvested
        #: stamps only: bins that were never occupied, or freed by eviction, are absent.
        self.emptied_at = emptied_at
        #: dict[BinKey, tuple[Bin, ...]] — occupied bins the standing demand clears,
        #: per the sim's own drain rule.  UNTIMED, and keyed like `empties` so an
        #: evaluator walks both tiers uniformly.
        self.predicted = predicted
        #: float | None — the release instant of the injected demand (a fact, not an
        #: estimate; None before any injection, e.g. a bare test manager).
        self.released_at = released_at
        #: (demand_v, reclaim_v, fill_v) at freeze — equality-only, see the module note.
        self.versions = versions
        #: float — the drain epoch this view froze at.
        self.frozen_at = frozen_at


class SpaceTimeline:
    """The incrementally-maintained timeline; one per arm, riding the standing yard.

    Owns the standing-demand injection, the harvested clear stamps and the three version
    counters; builds one frozen `SpaceView` per drain.  `freeze` is PURE with respect to
    the manager: it mutates no manager state and consumes no RNG (pinned by
    `Tests/unit/test_space_timeline.py`); its only writes are this object's own
    bookkeeping (`views_built`).
    """

    __slots__ = ('_drain', 'demand', 'released_at', 'emptied_at',
                 'demand_v', 'reclaim_v', 'fill_v', 'views_built')

    def __init__(self, drain_rule):
        #: The injected drain rule — `Workload_Builder.drain_sku`'s signature:
        #: (singleton_bins, pallet_bins, qty, out: defaultdict[Bin, int]) -> remaining.
        self._drain = drain_rule
        #: Standing demand, one batch deep: {sku: qty} = released batch + rollover carry.
        self.demand: dict = {}
        self.released_at: float | None = None
        #: id(bin) -> the absolute second it ran dry — harvested stamps, kept only while
        #: the bin stays empty (`fill` expires them; a re-emptied bin is re-harvested).
        self.emptied_at: dict = {}
        self.demand_v = 0
        self.reclaim_v = 0
        self.fill_v = 0
        #: Freeze count — non-vacuity handle for the neutrality tests, nothing more.
        self.views_built = 0

    def attach(self, mgr) -> 'SpaceTimeline':
        """Bind onto the manager instance — rebind, no listener registry (the
        `BinRecorder.attach` precedent).  The manager's hooks are `is None` tests on
        this one attribute, so detaching is unbinding and flag-off is never-bound."""
        mgr.space_timeline = self
        return self

    # ── the event feeds ───────────────────────────────────────────────────────────
    def inject_demand(self, items, released_at: float | None) -> None:
        """The driver's per-batch injection, BEFORE check_reorders: the batch about to
        be released plus the rollover carry.  REPLACES the standing demand — it is one
        batch deep by charter, never an accumulation.  `released_at` is the batch's
        release instant on the absolute clock (a fact the view carries verbatim)."""
        self.demand = dict(items)
        self.released_at = released_at
        self.demand_v += 1

    def harvest(self, bins, stamps) -> None:
        """The reclaim-harvest: called by `_reclaim_empty_bins` with `_pending_reclaim`
        and `_emptied_at` at the one moment they meet before both are wiped.  Every
        reclaimed bin bumps `reclaim_v` (the index changed for each); only bins with an
        actual stamp enter `emptied_at` (a stampless notification — a caller with no
        clock — frees the bin without asserting when)."""
        emptied_at = self.emptied_at
        for bin_ in bins:
            at = stamps.get(id(bin_))
            if at is not None:
                emptied_at[id(bin_)] = at
        self.reclaim_v += len(bins)

    def fill(self, bin_) -> None:
        """A placement occupied `bin_` (`_execute_placement`, the chokepoint every
        put-away funnels through): bump `fill_v` and expire the bin's clear stamp —
        `emptied_at` describes bins that are empty, and this one no longer is."""
        self.fill_v += 1
        self.emptied_at.pop(id(bin_), None)

    # ── the per-drain freeze ──────────────────────────────────────────────────────
    def freeze(self, mgr, frozen_at: float) -> SpaceView:
        """Build the drain's frozen view: snapshot the empties, project the predicted
        clears, stamp the version vector.  ONE projection per drain serves every
        decision in it.

        The projection runs the injected drain rule over the manager's live per-SKU bin
        indexes — the exact walk `Task.from_batch` will do when this demand is picked —
        and a bin is predicted-clear when its projected take equals its quantity.
        Per-SKU projections are independent (a bin holds one SKU), so demand order
        cannot change the set.
        """
        predicted: dict = {}
        singles = mgr._sku_singleton_bins
        pallets = mgr._sku_pallet_bins
        for sku, qty in self.demand.items():
            takes = defaultdict(int)
            self._drain(singles.get(sku, ()), pallets.get(sku, ()), qty, takes)
            for bin_, take in takes.items():
                if take == bin_.storage.quantity:
                    predicted.setdefault(mgr._key(bin_), []).append(bin_)
        # Snapshot, don't mirror: `_index` stays the single source of truth and its live
        # lists swap-remove mid-drain; the copied per-key tuples are what "frozen" means.
        # Keys whose list is empty carry no bins and are elided.
        empties = {key: tuple(bins) for key, bins in mgr._index.items() if bins}
        self.views_built += 1
        return SpaceView(
            empties=empties,
            emptied_at=dict(self.emptied_at),
            predicted={key: tuple(bins) for key, bins in predicted.items()},
            released_at=self.released_at,
            versions=(self.demand_v, self.reclaim_v, self.fill_v),
            frozen_at=frozen_at)
