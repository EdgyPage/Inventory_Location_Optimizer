"""transit — the trailer model on the manager's order-port seam.

`TrailerTransit` binds where `BatchTransit` (the flag-off default in
`Warehouse/inventory/inventory_reorder.py`) sits: same seam, same phase wrappers, injected
by the driver and never imported by anything under `Warehouse/` — the broker rule.  What
changes flag-on is the SHAPE of transit: fired reorders load item-by-item into trailers
(FIFO next-fit — a lot that outgrows the open trailer continues in a fresh one, which is
`inbound_split` realized structurally: each trailer's portion packs as its own delivery),
trailers dispatch when loading passes them by and arrive per their lead (seconds on the
absolute clock, minutes-authored, default ZERO = instantly), stand in the YARD,
stage to DOCK DOORS, and are worked in priority order — global over trailers at both dock
moments, local over load pallets, both `'fifo'` in v1.

# ── the lead, and why it is drawn here ────────────────────────────────────────────

A lead is PER TRAILER, drawn at creation from one lognormal — median `lead_s` × spread
`lead_sigma` — and homogeneous leads are that distribution's σ = 0 case, taken literally:
no generator is constructed and `lead_s` is returned as-is, so "the default is
byte-identical" is a property of the code path rather than a claim about a distribution.
The draw is stateless and SEQ-KEYED (`SeedSequence([seed, tag, seq])`, one draw,
discarded), which is what makes it survive this repo's constraints for free: nothing to
pickle across the spawn boundary, nothing to restore on a resume, and trailer #N drawing
the same lead in every arm of a run — common random numbers across arms.  Heterogeneity
is the standing yard's alone (`inbound_spec` refuses a spread without it): v1 ranks by
dispatch seq, so a spread there would shift arrival batches without scrambling any order.

# ── what the doors do in v1, and deliberately do not ──────────────────────────────

Doors are BOOKKEEPING in v1: every arrival lands at the batch epoch and every policy is
FIFO, so a per-batch door throttle would invent staffing physics the shift-end decision
already declined — the crew's hours are the bound on receiving, exactly as the dock's
no-backpressure note says.  The door count, free-door census and staging order are all
real and reported through `DockContext`, so the policies that make doors bite (arrival
spread, non-FIFO staging) arrive as registry entries, not as rewiring.

`YardTransit` below is where doors BECOME real: at most `doors` trailers staged, a trailer
holds its door across drains until fully unloaded, and the yard-pull fires when a door
frees.  Gated by `INBOUND_STANDING_YARD`; flag-off binds this class untouched.

# ── the seam contract ─────────────────────────────────────────────────────────────

Mirrors `BatchTransit` exactly — `SOURCE`, `dispatch`, `advance`, `release`, `depth`,
`merchandise()`, `snapshot()` — so the manager's phase bodies cannot tell which transit is
bound.  `release()` returns `[sku, qty, remaining]` DELIVERIES (one per contiguous trailer
lot, in unload order); the manager's ledger debit, packer and admission flow are untouched,
and admissions carry `SOURCE = 'trailer'`, the fourth `PutawayItem` provenance.
"""
from __future__ import annotations

from math import exp

import numpy as np

from Inbound.priorities import (
    DockContext, bounded_order, dock_key, global_key, local_key, yard_key)
from Inbound.trailer import Trailer, Trailer53

#: The lead draw's DOMAIN TAG — the middle entropy word that keeps this stream disjoint
#: from every other consumer of the world seed.  A literal, never derived: its whole job
#: is to be stable, because changing it re-rolls every lead schedule in the archive.
_LEAD_TAG: int = 0x1EAD


class TrailerTransit:
    """Trailers from the order port to the dock doors, in one object."""

    SOURCE = 'trailer'

    __slots__ = ('trailer_type', 'lead_s', 'lead_sigma', 'lead_seed', 'doors',
                 '_global', '_local', 'bound', '_open', '_in_transit', '_yard', '_seq')

    def __init__(self, trailer_type: type = None, *, lead_s: float = 0.0,
                 lead_sigma: float = 0.0, lead_seed: int = 0,
                 doors: int = 4, global_policy: str = 'fifo',
                 local_policy: str = 'fifo', bound: int | None = None):
        self.trailer_type = trailer_type if trailer_type is not None else Trailer53
        # `lead_s` is the MEDIAN once `lead_sigma` is positive, and the whole lead when it
        # is zero — the same number either way, which is why the field did not need renaming.
        self.lead_s = float(lead_s)
        self.lead_sigma = float(lead_sigma)
        self.lead_seed = int(lead_seed)
        self.doors = int(doors)
        self._global = global_key(global_policy)
        self._local = local_key(local_policy)
        self.bound = bound
        self._open: Trailer | None = None       # loading at the ordering site
        self._in_transit: list = []             # dispatched, lead not yet elapsed
        self._yard: list = []                   # arrived, awaiting a door
        self._seq = 0                           # dispatch counter — FIFO's tiebreak

    # ── the lead draw (seq-keyed, stateless, and ABSENT at spread zero) ──────────
    def lead_for(self, seq: int) -> float:
        """Trailer `seq`'s transit lead, in absolute-clock seconds.

        Spread 0 — the default, and every run before this existed — returns the median
        UNTOUCHED, constructing no generator and consuming no entropy.  The scalar path is
        not re-derived through a degenerate distribution; it is the same expression it
        always was, which is why the byte-identity claim here is a property of the code
        rather than a tolerance on a comparison.

        Otherwise `median · exp(σ · Z_seq)`, lognormal: strictly positive, so nothing needs
        clamping, and with the right tail that does the work — stragglers overtaken by
        later dispatches (the ordering lever's gradient) and trailers at risk of crossing
        the fee threshold while they stand.

        A PURE FUNCTION of `(seed, tag, seq)`, deliberately: no generator object exists to
        pickle across the spawn boundary or restore on a resume, drawing lead #7 never
        depends on whether #0..#6 were drawn first, and trailer #N gets the same lead in
        every arm of a run — common random numbers across arms, at no cost.
        """
        if self.lead_sigma <= 0.0:
            return self.lead_s
        rng = np.random.default_rng(
            np.random.SeedSequence([self.lead_seed, _LEAD_TAG, int(seq)]))
        return self.lead_s * exp(self.lead_sigma * float(rng.standard_normal()))

    # ── the order port (called from _fire_reorders) ──────────────────────────────
    def dispatch(self, sku: int, qty: int, lead: int,
                 unit_volume: int | None = None, now_s: float | None = None) -> None:
        """Load one fired reorder, item-by-item, FIFO next-fit across trailers.

        `lead` (batches) is the LEGACY denomination and is deliberately ignored here: a
        trailer's lead is seconds on the absolute clock, drawn per trailer by `lead_for`
        (ticket: "Choose the lead-time denomination").  `unit_volume` None falls back to
        one item per position-volume share of nothing — callers on this seam pass
        `order.volume()`.

        The draw happens HERE, at creation, and never again: the lead is a property of the
        trailer from the instant it exists, so `dispatched_s + lead_s` is a fixed arrival
        the yard can be sorted by however many drains later it is observed.
        """
        vol = int(unit_volume) if unit_volume else 1
        left = int(qty)
        while left > 0:
            fresh = self._open is None
            if fresh:
                self._open = Trailer(self.trailer_type, self._seq,
                                     lead_s=self.lead_for(self._seq),
                                     dispatched_s=now_s)
                self._seq += 1
            took = self._open.load(sku, left, vol)
            left -= took
            if left > 0:
                if fresh and took == 0:
                    # fits no trailer at all (an item wider than a pallet position);
                    # break rather than spin — the conservation checks surface the loss.
                    break
                # the open trailer is full for this item — it leaves, FIFO preserved
                self._depart(self._open)
                self._open = None

    def _depart(self, trailer: Trailer) -> None:
        self._in_transit.append(trailer)

    # ── the calendar (phase wrappers delegate here) ──────────────────────────────
    def advance(self) -> None:
        """Leads are absolute-clock seconds; a batch tick moves nothing by itself."""

    def release(self, now_s: float | None = None) -> list:
        """Everything the dock can start on this batch, as [sku, qty, 0] deliveries.

        Order of operations IS the model: (1) anything still loading departs — a trailer
        waits for nothing in v1; (2) arrivals (lead elapsed against `now_s`) join the
        yard; (3) the yard stages to doors and staged trailers are worked in
        GLOBAL-priority order, each trailer's pallets in LOCAL-priority order, emitting one
        delivery per contiguous SKU lot — so a split shipment packs as the pieces it
        arrived in.  Worked trailers free their doors within the drain (v1: doors are
        bookkeeping; see the module note).
        """
        if self._open is not None:
            self._depart(self._open)
            self._open = None
        still = []
        for t in self._in_transit:
            (self._yard if t.arrived(now_s) else still).append(t)
        self._in_transit = still

        deliveries: list = []
        ctx = DockContext(doors=self.doors, free_doors=self.doors,
                          yard_depth=len(self._yard))
        for trailer in bounded_order(self._yard, self._global, ctx, self.bound):
            trailer.staged = True
            mine: list = []
            indexed = list(enumerate(trailer.pallets))
            for _i, pallet in bounded_order(indexed, self._local, ctx, None):
                for sku, qty, _vol in pallet.lots:
                    if mine and mine[-1][0] == sku:
                        # contiguous same-SKU lots across pallets of ONE trailer merge:
                        # they were one reorder and pack as one delivery portion.  Never
                        # merged ACROSS trailers — each trailer's portion packing on its
                        # own is the whole split model.
                        mine[-1][1] += qty
                    else:
                        mine.append([sku, qty, 0])
            deliveries.extend(mine)
            trailer.staged = False
        self._yard = []
        return deliveries

    # ── the census (levels the ledger and replay read) ───────────────────────────
    @property
    def depth(self) -> int:
        """In-flight ENTRIES: trailers loading, in transit, or standing in the yard."""
        n = len(self._in_transit) + len(self._yard)
        return n + (1 if self._open is not None else 0)

    def merchandise(self) -> int:
        """Pieces not yet released to the dock — the in_transit_qty level."""
        total = 0
        for t in ([self._open] if self._open else []) + self._in_transit + self._yard:
            total += sum(qty for _s, qty, _v in t.lots())
        return total

    def snapshot(self) -> list:
        """(sku, qty, remaining_lead) tuples for the replay rows; remaining is 1 for
        anything still riding a lead, 0 for standing — batches were never its unit."""
        out = []
        for t in ([self._open] if self._open else []) + self._in_transit:
            out.extend((sku, qty, 1) for sku, qty in sorted(t.sku_totals().items()))
        for t in self._yard:
            out.extend((sku, qty, 0) for sku, qty in sorted(t.sku_totals().items()))
        return out


class YardTransit(TrailerTransit):
    """The standing yard: doors become real, and a trailer keeps its door until empty.

    What changes against the parent, and nothing else: `release()` is PURE CALENDAR —
    departs loaders, lands arrivals in the yard, returns NO deliveries — and every door
    and crew decision moves to `_receive`, which drives the standing surfaces below over
    DRAIN-FROZEN rankings.  The parent's `release()` body is not edited; flag-off binds
    the parent untouched, which is the first of the four byte-identity layers.

    # ── the standing surfaces (what `_receive` drives) ────────────────────────────

    The manager owns `_originals`, the packer and the crew, so it cannot live here — and
    this class may not reach any of those, so the split is: THIS object holds trailer,
    yard and door STATE (`unplanned`/`planned_lots` for plans-at-arrival, `freeze_ctx`/
    `yard_order`/`dock_order` for the frozen rankings, `stage`/`door_freed`/`discard` for
    the door lifecycle, `staged`/`free_doors` for the census) and the MANAGER makes every
    decision over them.  The other transits satisfy these surfaces trivially — no
    `STANDING` attribute, no standing work — which is why neither is edited.

    # ── invariants worth naming ───────────────────────────────────────────────────

    * The yard is kept in (arrival stamp, `seq`) order — the charter's entry order — so
      the stable ranking sort breaks stamp ties by seq for free.
    * At most `doors` trailers are staged; `stage` raises past that rather than quietly
      widening the dock.
    * Decisions are drain-quantized, data is event-stamped: `stage`/`door_freed` take the
      ABSOLUTE instant the caller derived (drain epoch, or epoch + crew-clock offset for
      a mid-drain refill), and `stamps` keeps one `(seq, arrived_s, staged_s, emptied_s)`
      tuple per finished trailer — the raw material the yard-metrics ticket reports from,
      kept here because the trailer object itself is dropped when it empties.
    * `INBOUND_GLOBAL_POLICY` and the parent's global registry are UNREAD here — the
      yard/dock registries are the standing model's split of that decision.
    """

    #: What `_receive` probes (via getattr, default False) to find the standing surfaces.
    STANDING = True

    __slots__ = ('allocation', '_yard_key', '_dock_key', '_staged', 'stamps',
                 'gain_bundle')

    def __init__(self, trailer_type: type = None, *, lead_s: float = 0.0,
                 lead_sigma: float = 0.0, lead_seed: int = 0,
                 doors: int = 4, yard_policy: str = 'fifo', dock_policy: str = 'fifo',
                 local_policy: str = 'fifo', bound: int | None = None,
                 allocation: str = 'split'):
        super().__init__(trailer_type, lead_s=lead_s, lead_sigma=lead_sigma,
                         lead_seed=lead_seed, doors=doors,
                         global_policy='fifo', local_policy=local_policy, bound=bound)
        self._yard_key = yard_key(yard_policy)
        self._dock_key = dock_key(dock_policy)
        if allocation not in ('split', 'merged'):
            raise ValueError(f'unknown crew allocation {allocation!r}; '
                             f"known: 'split', 'merged'")
        # A mechanics MODE, not a scoring policy — hence a plain enum, not a registry.
        # 'split' deals door teams (the standing model's physics); 'merged' is the v1
        # pooled gang, kept as honest physics and as the lockstep verification bridge.
        self.allocation = allocation
        self._staged: list = []       # holding a door, in staging order
        self.stamps: list = []        # (seq, arrived_s, staged_s, emptied_s) per finished
        # The gain arms' machinery (`Inbound.gain.GainBundle`), assigned by the DRIVER
        # after construction when a gain policy is named — injected, never imported
        # (the broker rule); None otherwise, and the seeded keys never read it.
        self.gain_bundle = None

    # ── the calendar (all that release() does here) ───────────────────────────────
    def release(self, now_s: float | None = None) -> list:
        """Pure calendar: departs loaders, lands arrivals in the yard, delivers NOTHING.

        The unload pull lives in `_receive` because it is LABOUR — a lead elapses whether
        or not anyone is at work, but nothing comes off a trailer without a crew.  An
        empty return keeps `_release_arrivals` a structural no-op: no ledger debit, no
        packing, no admission — the deferred-until-unload contract.
        """
        if self._open is not None:
            self._depart(self._open)
            self._open = None
        still = []
        for t in self._in_transit:
            if t.arrived(now_s):
                # The EVENT stamp, not the drain's: a trailer whose lead elapsed at four
                # o'clock arrived at four o'clock, however late the drain observes it.
                if t.dispatched_s is not None:
                    t.arrived_s = t.dispatched_s + t.lead_s
                else:
                    t.arrived_s = now_s
                self._yard.append(t)
            else:
                still.append(t)
        self._in_transit = still
        # Charter order: arrival stamp, seq as tiebreak.  Sorted here, once per drain, so
        # every consumer (ranking, census, FIFO's stable-tie fallback) sees one order.
        self._yard.sort(key=lambda t: (
            t.arrived_s if t.arrived_s is not None else float('-inf'), t.seq))
        return []

    # ── plans-at-arrival (the manager packs; this hands it the lots) ──────────────
    def unplanned(self) -> list:
        """Yard trailers with no pack plan yet — arrived this drain, in yard order."""
        return [t for t in self._yard if t.pending is None]

    def planned_lots(self, trailer: Trailer, ctx: DockContext) -> list:
        """(sku, qty) per contiguous lot, in canonical LOCAL-priority order — the exact
        portions the parent's `release()` would have emitted as deliveries, so the pack
        plan is v1's, fixed at loading, whatever later interrupts the unload."""
        lots: list = []
        indexed = list(enumerate(trailer.pallets))
        for _i, pallet in bounded_order(indexed, self._local, ctx, None):
            for sku, qty, _vol in pallet.lots:
                if lots and lots[-1][0] == sku:
                    lots[-1][1] += qty
                else:
                    lots.append([sku, qty])
        return [(sku, qty) for sku, qty in lots]

    def discard(self, trailer: Trailer, at_s: float | None) -> None:
        """Drop a yard trailer whose plan packed NOTHING (shortfall to zero) — there is
        no work to stage a door for.  The ledger debit is the manager's, done where the
        shortfall was measured; this only keeps the yard free of undrainable entries."""
        self._yard.remove(trailer)
        trailer.emptied_s = at_s
        self.stamps.append((trailer.seq, trailer.arrived_s, trailer.staged_s, at_s))

    # ── the frozen rankings and the door lifecycle ────────────────────────────────
    def freeze_ctx(self) -> DockContext:
        """The drain's frozen context: computed once, before any staging or unload.
        `ctx.gain` rides here the way `ctx.space` rides the manager's freeze — the
        injected bundle if a gain policy runs, None otherwise."""
        ctx = DockContext(doors=self.doors,
                          free_doors=self.doors - len(self._staged),
                          yard_depth=len(self._yard))
        ctx.gain = self.gain_bundle
        return ctx

    def yard_order(self, ctx: DockContext) -> list:
        """The drain-frozen YARD ranking: which standing trailer takes the next freed
        door.  Consumed front-first by every same-drain refill — no mid-drain re-score."""
        return bounded_order(list(self._yard), self._yard_key, ctx, self.bound)

    def dock_order(self, ctx: DockContext) -> list:
        """The drain-frozen DOCK ranking over staged trailers: the crew-allocation
        preference, and the canonical handoff order's first key."""
        return bounded_order(list(self._staged), self._dock_key, ctx, self.bound)

    @property
    def free_doors(self) -> int:
        return self.doors - len(self._staged)

    def staged(self) -> list:
        return list(self._staged)

    def stage(self, trailer: Trailer, at_s: float | None) -> None:
        """Move one yard trailer to a door.  `at_s` is the event instant: the drain epoch
        for the whistle-independent fill, epoch + crew-clock offset for a refill."""
        if len(self._staged) >= self.doors:
            raise RuntimeError(f'all {self.doors} doors are occupied — the caller must '
                               f'pull only when a door is free')
        self._yard.remove(trailer)
        self._staged.append(trailer)
        trailer.staged = True
        trailer.staged_s = at_s

    def door_freed(self, trailer: Trailer, at_s: float | None) -> None:
        """A trailer came up empty: stamp it, free its door, and drop its plan.

        The pack plan and pending list die here (the LoadPlan-retention lesson — nothing
        may pin a run's worth of live StorageUnits); the stamps survive in `stamps`.
        """
        self._staged.remove(trailer)
        trailer.staged = False
        trailer.emptied_s = at_s
        trailer.plans = None
        trailer.pending = None
        trailer.taken = 0
        self.stamps.append((trailer.seq, trailer.arrived_s, trailer.staged_s, at_s))

    # ── the census (yard + staged remainders, so conservation reads true) ─────────
    @property
    def depth(self) -> int:
        """In-flight ENTRIES: loading, in transit, standing in the yard, or staged with
        a remainder — a trailer leaves the census only when its last unit comes off."""
        return super().depth + len(self._staged)

    def merchandise(self) -> int:
        """Pieces not yet handed to the put queue — mirrors the deferred ledger: full
        loads before arrival, PLANNED quantities once a plan exists (a packing shortfall
        was already debited at arrival), remainders only on staged trailers."""
        total = 0
        for t in ([self._open] if self._open else []) + self._in_transit:
            total += sum(qty for _s, qty, _v in t.lots())
        for t in self._yard + self._staged:
            total += t.remaining_qty()
        return total

    def snapshot(self) -> list:
        """(sku, qty, remaining_lead) rows: 1 riding a lead, 0 standing — yard trailers
        whole, staged trailers by their remainders."""
        out = []
        for t in ([self._open] if self._open else []) + self._in_transit:
            out.extend((sku, qty, 1) for sku, qty in sorted(t.sku_totals().items()))
        for t in self._yard + self._staged:
            out.extend((sku, qty, 0) for sku, qty in sorted(t.remaining_totals().items()))
        return out

