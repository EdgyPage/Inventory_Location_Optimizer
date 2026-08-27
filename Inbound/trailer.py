"""trailer — the vehicle, its load pallets, and the FIFO loading that fills them.

# ── the two-level object model ────────────────────────────────────────────────────

`TrailerType` is stateless class-as-config on the `StorageCart` pattern
(`Warehouse/layout/Storage_Primitive.py`): capacity read off the TYPE, one subclass per
real vehicle.  Positions come from footprint arithmetic at the 48-inch square pallet
(`Warehouse.physical.PALLET_FOOTPRINT`), single-stacked: a 53-foot trailer is 13 rows x 2
across = 26 positions; a 28-foot pup is 6 x 2 = 12.  Both are overridable class attributes,
and a mixed fleet is a future policy the type seam permits — one run uses one type today.

`Trailer` is the stateful half and persists day over day: contents, remaining volume, an
optional lead, the arrival stamp, door state.  Its pack plan is computed once at arrival
(fixed by its full per-SKU contents — crew interruption pauses packing WORK, never changes
the packs) and dropped when the trailer is fully worked, so nothing pins packs across
batches — the constraint the removed LoadPlan-retention leak taught.

# ── the load pallet, and why it is not a pack ─────────────────────────────────────

A LOAD PALLET is the transport grouping loose items ride on — it may MIX SKUs that fit its
48-cubed volume, and it is what the receiving crew pulls.  It is NOT a pack: packing has
not happened yet (root `CONTEXT.md` carries both entries with cross-avoid notes).  Fit is
volumetric with the perfect-packing assumption verbatim from `StorageCart.add_from_bin`;
next-fit closes a pallet, pallets fill positions, and an item that fits no remaining
position starts a NEW trailer — which is what keeps loading strictly FIFO ("placed
perfectly in FIFO order into a trailer if it fits").

FIFO loading makes SKU lots CONTIGUOUS by construction: all of one reorder loads before the
next begins, so "three pallets of x" sit together and the local-priority key's candidate is
the load pallet, degenerating to the SKU lot on single-SKU pallets.
"""
from __future__ import annotations

from Warehouse.physical import PALLET_FOOTPRINT

#: One pallet position's volume: the 48-inch square footprint, one tier high.
POSITION_VOLUME: int = PALLET_FOOTPRINT ** 3


class LoadPallet:
    """One transport grouping of loose items: (sku, qty, unit_volume) lots that fit 48^3."""

    __slots__ = ('_remaining', 'lots')

    def __init__(self):
        self._remaining: int = POSITION_VOLUME
        self.lots: list = []          # [sku, qty, unit_volume] — mutable, mixed SKUs fine

    @property
    def remaining_volume(self) -> int:
        return self._remaining

    def add(self, sku: int, qty: int, unit_volume: int) -> int:
        """Take up to `qty` items volumetrically; returns how many fit (perfect packing,
        the `StorageCart.add_from_bin` assumption verbatim)."""
        if qty <= 0 or unit_volume <= 0 or unit_volume > self._remaining:
            return 0
        took = min(qty, self._remaining // unit_volume)
        if took <= 0:
            return 0
        self._remaining -= took * unit_volume
        if self.lots and self.lots[-1][0] == sku:
            self.lots[-1][1] += took
        else:
            self.lots.append([sku, took, unit_volume])
        return took


class TrailerType:
    """Stateless config: how many pallet positions a vehicle holds. Subclass per vehicle."""

    pallet_positions: int = 26

    @classmethod
    def capacity(cls) -> int:
        """Total loose-item volume, positions x one max pallet each."""
        return cls.pallet_positions * POSITION_VOLUME


class Trailer53(TrailerType):
    """53-foot trailer: 13 rows x 2 across of 48-inch pallets, single-stacked."""
    pallet_positions: int = 26


class Trailer28(TrailerType):
    """28-foot pup: 6 rows x 2 across, single-stacked."""
    pallet_positions: int = 12


TRAILER_TYPES: dict = {'53': Trailer53, '28': Trailer28}


class Trailer:
    """One vehicle in flight or on the ground.  State matters and persists day over day."""

    __slots__ = ('type', 'pallets', 'lead_s', 'dispatched_s', 'seq',
                 'staged', 'plans')

    def __init__(self, trailer_type: type, seq: int, lead_s: float = 0.0,
                 dispatched_s: float | None = None):
        self.type = trailer_type
        self.pallets: list = [LoadPallet()]
        self.lead_s = float(lead_s)
        self.dispatched_s = dispatched_s   # order-port epoch; None when no clock was known
        self.seq = int(seq)                # dispatch order — FIFO's arrival tiebreak
        self.staged = False                # holds a dock door
        self.plans = None                  # pack plans, computed once at arrival

    def __repr__(self):
        return (f'Trailer({self.type.__name__}, #{self.seq}, '
                f'{len(self.pallets)} pallet(s), {sum(l[1] for l in self.lots())} items)')

    # ── loading (the unmodeled facility's one modeled rule: FIFO next-fit) ───────
    def load(self, sku: int, qty: int, unit_volume: int) -> int:
        """Next-fit at the pallet level: fill the open pallet, then a new one while
        positions remain.  Returns how many items fit; the remainder starts a new trailer
        (the caller's next-fit, which is what preserves FIFO)."""
        left = qty
        while left > 0:
            took = self.pallets[-1].add(sku, left, unit_volume)
            left -= took
            if left > 0:
                if len(self.pallets) >= self.type.pallet_positions:
                    break
                if took == 0 and unit_volume > POSITION_VOLUME:
                    break                      # can never fit anywhere
                self.pallets.append(LoadPallet())
        return qty - left

    def lots(self) -> list:
        """Every (sku, qty, unit_volume) lot in pallet order — contiguous by construction."""
        return [lot for p in self.pallets for lot in p.lots]

    def sku_totals(self) -> dict:
        """{sku: qty} of the whole load — what fixes the pack plan at arrival."""
        out: dict = {}
        for sku, qty, _v in self.lots():
            out[sku] = out.get(sku, 0) + qty
        return out

    def arrived(self, now_s: float | None) -> bool:
        """Has this trailer reached the parking lot?

        Lead zero (the decided default) arrives instantly.  A positive lead needs the
        absolute clock; when no clock reaches the calendar (`now_s` None — bare test
        managers), a positive lead simply has not arrived, which fails safe: merchandise
        waits rather than teleports.
        """
        if self.lead_s <= 0.0:
            return True
        if now_s is None or self.dispatched_s is None:
            return False
        return (self.dispatched_s + self.lead_s) <= now_s
