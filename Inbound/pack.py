"""inbound — what arrived, how it packed, and what a single delivery would have produced.

# ── why packing belongs to the ARRIVAL, not to the order ──────────────────────────

`viable_storage_units(order, quantity)` has always taken a quantity, so the packing has
always been a function of how much shows up at once. Nothing named that, so nothing could
ask the interesting question: *an order that would be palletized if it came in all at once,
but arrives split across two trailers and lands as two singleton packs instead.*

That is not hypothetical. Measured over the first twelve orders of a planned 200-SKU
catalogue, splitting each delivery in half changed the packing for **five of them**:

    sku  52   eq   8    whole  3 pallets    split in two  4 pallets
    sku 178   eq  14    whole  5 pallets    split in two  6 pallets
    sku  21   eq 137    whole 34 pallets + 26 singletons  split  40 pallets + 0 singletons
    sku  51   eq   3    whole  2 pallets    split in two  3 pallets
    sku  77   eq  44    whole 27 pallets    split in two 24 pallets

**Both directions occur, and they have different causes.** Default packing — fill pallets,
route the remainder to a singleton — is monotone: splitting it can only produce the same
units or more. The negative cases come from `stock_plan`, which overrides the packer with a
run-length list of `(is_singleton, per, count)` slots filled from the front. A whole arrival
that runs off the end of the pallet slots spills into the plan's singleton tail; each half of
the same arrival stops before that tail and stays on pallets. So on a **planned** SKU — which
is most of a generated catalogue — "splitting is worse" is not a safe assumption, and the
number has to be looked at rather than signed in advance.

# ── what this module adds, and what it deliberately does not ──────────────────────

It adds a NAME and a RECORD. `receive()` calls `viable_storage_units` unchanged and wraps the
result in a `LoadPlan` that knows what it received, what it packed into, and — on demand —
what the same order would have packed into arriving whole. Nothing about the packing
behaviour moves.

It does NOT model trailers, docks, or how a shipment gets split. That is the caller's
business: `receive_all()` takes the split as a list of quantities and says nothing about
where the list came from. The point of the seam is that a trailer model can be written
against it without touching the packer.

# ── the counterfactual is lazy ────────────────────────────────────────────────────

`unsplit()` re-packs at the whole quantity, which costs as much as the packing itself. It is
a method rather than a field so the ordinary receive path pays nothing for a comparison
nobody asked for.
"""
from __future__ import annotations

from Warehouse.layout.Storage_Primitive import viable_storage_units

#: `unit_category` values, restated from put_queue so a caller reading a LoadPlan does not
#: have to import the queue layer to interpret one.
PALLET = 'pallet'
SINGLETON = 'singleton'
FULFILLMENT = 'fulfillment'


class LoadPlan:
    """One inbound delivery: what arrived, and the storage units it became.

    Immutable in the sense that matters -- the units are packed once at receipt and the plan
    is the record of that. `whole_qty` is the quantity this delivery WOULD have been had the
    shipment not been split, and it is what makes `unsplit()` answerable; it equals
    `received` for a delivery that was not split.
    """

    __slots__ = ('order', 'received', 'units', 'whole_qty', 'index', 'of')

    def __init__(self, order, received: int, units, whole_qty: int | None = None,
                 index: int = 0, of: int = 1):
        self.order = order
        self.received = int(received)
        self.units = tuple(units)
        self.whole_qty = int(received if whole_qty is None else whole_qty)
        #: Which delivery of the shipment this is, and how many there were. A single
        #: delivery is 0 of 1, which is what `was_split` reads.
        self.index = int(index)
        self.of = int(of)

    def __repr__(self):
        mix = '+'.join(f'{n}{k[0].upper()}' for k, n in sorted(self.tier_mix.items()))
        return (f'LoadPlan(sku={self.sku}, received={self.received}, {mix}, '
                f'{self.index + 1}/{self.of})')

    # ── what arrived ──────────────────────────────────────────────────────────────
    @property
    def sku(self) -> int:
        return self.order.sku

    @property
    def was_split(self) -> bool:
        return self.of > 1

    @property
    def tier_mix(self) -> dict:
        """`{unit_category: count}` for this delivery."""
        out: dict = {}
        for u in self.units:
            out[u.unit_category] = out.get(u.unit_category, 0) + 1
        return out

    @property
    def unit_count(self) -> int:
        return len(self.units)

    @property
    def packed_qty(self) -> int:
        """Units of MERCHANDISE packed. Equals `received` unless the packer could not place
        some of it, which is worth being able to see rather than assume away."""
        return sum(u.quantity for u in self.units)

    # ── what it would have been ───────────────────────────────────────────────────
    def unsplit(self) -> 'LoadPlan':
        """The plan the WHOLE shipment would have produced, arriving at once.

        Re-packs at `whole_qty`, which costs as much as the original packing -- hence a
        method and not a field. For a delivery that was not split this returns an equivalent
        plan, so a caller can compare unconditionally.
        """
        return LoadPlan(self.order, self.whole_qty,
                        viable_storage_units(self.order, self.whole_qty))

    def split_penalty(self) -> int:
        """Extra storage units this delivery pattern cost, against arriving whole.

        NEGATIVE when splitting helped -- which happens on any SKU carrying a `stock_plan`,
        and is the reason this returns a signed number rather than a count of regret. sku 21
        above packs into 60 units whole and 40 split, because each half stops short of the
        plan's singleton tail.

        Meaningful only summed over a whole shipment: one delivery of a split shipment is
        being compared against the whole thing, so its individual value is not interesting.
        """
        return self.unit_count - self.unsplit().unit_count


def receive(order, quantity: int, *, whole_qty: int | None = None,
            index: int = 0, of: int = 1) -> LoadPlan:
    """Pack one inbound delivery. The packer is unchanged; this names the result.

    `whole_qty` is the shipment's total when this is one delivery of several, and is what
    the counterfactual is measured against.
    """
    return LoadPlan(order, quantity, viable_storage_units(order, quantity),
                    whole_qty=whole_qty, index=index, of=of)


def receive_all(order, deliveries) -> list:
    """Pack a shipment that arrives as several deliveries.

    `deliveries` is a list of quantities -- how the shipment was split, and nothing about
    WHY. Trailers, docks and load planning are the caller's business; this seam exists so
    they can be written without touching the packer.

    Every returned plan carries the shipment total, so `split_penalty()` is answerable on
    each and meaningful when summed.
    """
    qtys = [int(q) for q in deliveries if int(q) > 0]
    total = sum(qtys)
    return [receive(order, q, whole_qty=total, index=i, of=len(qtys))
            for i, q in enumerate(qtys)]


def shipment_penalty(plans) -> int:
    """Extra storage units a whole shipment's split cost, against arriving at once.

    The number `split_penalty()` is a component of. Positive means the split produced more,
    smaller packs -- more put-away trips for the same merchandise. Negative means it
    produced fewer, which is real and happens when the halves fall on cleaner boundaries.
    """
    plans = list(plans)
    if not plans:
        return 0
    got = sum(p.unit_count for p in plans)
    return got - plans[0].unsplit().unit_count
