"""transit — the trailer model on the manager's order-port seam.

`TrailerTransit` binds where `BatchTransit` (the flag-off default in
`Warehouse/inventory/inventory_reorder.py`) sits: same seam, same phase wrappers, injected
by the driver and never imported by anything under `Warehouse/` — the broker rule.  What
changes flag-on is the SHAPE of transit: fired reorders load item-by-item into trailers
(FIFO next-fit — a lot that outgrows the open trailer continues in a fresh one, which is
`inbound_split` realized structurally: each trailer's portion packs as its own delivery),
trailers dispatch when loading passes them by and arrive per their lead (seconds on the
absolute clock, minutes-authored, default ZERO = instantly), stand in the PARKING LOT,
stage to DOCK DOORS, and are worked in priority order — global over trailers at both dock
moments, local over load pallets, both `'fifo'` in v1.

# ── what the doors do in v1, and deliberately do not ──────────────────────────────

Doors are BOOKKEEPING in v1: every arrival lands at the batch epoch and every policy is
FIFO, so a per-batch door throttle would invent staffing physics the shift-end decision
already declined — the crew's hours are the bound on receiving, exactly as the dock's
no-backpressure note says.  The door count, free-door census and staging order are all
real and reported through `DockContext`, so the policies that make doors bite (arrival
spread, non-FIFO staging) arrive as registry entries, not as rewiring.

# ── the seam contract ─────────────────────────────────────────────────────────────

Mirrors `BatchTransit` exactly — `SOURCE`, `dispatch`, `advance`, `release`, `depth`,
`merchandise()`, `snapshot()` — so the manager's phase bodies cannot tell which transit is
bound.  `release()` returns `[sku, qty, remaining]` DELIVERIES (one per contiguous trailer
lot, in unload order); the manager's ledger debit, packer and admission flow are untouched,
and admissions carry `SOURCE = 'trailer'`, the fourth `PutawayItem` provenance.
"""
from __future__ import annotations

from Inbound.priorities import DockContext, bounded_order, global_key, local_key
from Inbound.trailer import Trailer, Trailer53


class TrailerTransit:
    """Trailers from the order port to the dock doors, in one object."""

    SOURCE = 'trailer'

    __slots__ = ('trailer_type', 'lead_s', 'doors', '_global', '_local', 'bound',
                 '_open', '_in_transit', '_lot', '_seq')

    def __init__(self, trailer_type: type = None, *, lead_s: float = 0.0,
                 doors: int = 4, global_policy: str = 'fifo',
                 local_policy: str = 'fifo', bound: int | None = None):
        self.trailer_type = trailer_type if trailer_type is not None else Trailer53
        self.lead_s = float(lead_s)
        self.doors = int(doors)
        self._global = global_key(global_policy)
        self._local = local_key(local_policy)
        self.bound = bound
        self._open: Trailer | None = None       # loading at the ordering site
        self._in_transit: list = []             # dispatched, lead not yet elapsed
        self._lot: list = []                    # arrived, awaiting a door
        self._seq = 0                           # dispatch counter — FIFO's tiebreak

    # ── the order port (called from _fire_reorders) ──────────────────────────────
    def dispatch(self, sku: int, qty: int, lead: int,
                 unit_volume: int | None = None, now_s: float | None = None) -> None:
        """Load one fired reorder, item-by-item, FIFO next-fit across trailers.

        `lead` (batches) is the LEGACY denomination and is deliberately ignored here: a
        trailer's lead is `lead_s` on the absolute clock (ticket: "Choose the lead-time
        denomination").  `unit_volume` None falls back to one item per position-volume
        share of nothing — callers on this seam pass `order.volume()`.
        """
        vol = int(unit_volume) if unit_volume else 1
        left = int(qty)
        while left > 0:
            fresh = self._open is None
            if fresh:
                self._open = Trailer(self.trailer_type, self._seq,
                                     lead_s=self.lead_s, dispatched_s=now_s)
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
        parking lot; (3) the lot stages to doors and staged trailers are worked in
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
            (self._lot if t.arrived(now_s) else still).append(t)
        self._in_transit = still

        deliveries: list = []
        ctx = DockContext(doors=self.doors, free_doors=self.doors,
                          lot_depth=len(self._lot))
        for trailer in bounded_order(self._lot, self._global, ctx, self.bound):
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
        self._lot = []
        return deliveries

    # ── the census (levels the ledger and replay read) ───────────────────────────
    @property
    def depth(self) -> int:
        """In-flight ENTRIES: trailers loading, in transit, or parked."""
        n = len(self._in_transit) + len(self._lot)
        return n + (1 if self._open is not None else 0)

    def merchandise(self) -> int:
        """Pieces not yet released to the dock — the in_transit_qty level."""
        total = 0
        for t in ([self._open] if self._open else []) + self._in_transit + self._lot:
            total += sum(qty for _s, qty, _v in t.lots())
        return total

    def snapshot(self) -> list:
        """(sku, qty, remaining_lead) tuples for the replay rows; remaining is 1 for
        anything still riding a lead, 0 for parked — batches were never its unit."""
        out = []
        for t in ([self._open] if self._open else []) + self._in_transit:
            out.extend((sku, qty, 1) for sku, qty in sorted(t.sku_totals().items()))
        for t in self._lot:
            out.extend((sku, qty, 0) for sku, qty in sorted(t.sku_totals().items()))
        return out

