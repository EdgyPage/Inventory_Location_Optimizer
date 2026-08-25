"""put_queue.py — one configurable put-away queue, instantiated N times.

# ── why this is one object and not three ──────────────────────────────────────────

A store warehouse has at least two put-away streams that share nothing operationally: loose
singletons that a person walks into a cart, and pallets that need a forklift. Fulfillment has
a third. They differ in who does the work, how fast, how much can be staged, and how much
freedom the assignment policy gets — pallets go away in arrival order almost regardless of
what a scoring function would prefer, because there is no floor to lay a hundred of them out
on and re-sort them.

Writing three classes would encode today's three streams as the shape of the code. The axes
above are the real content, so they are a SPEC and the queue is one object configured by it.
A fourth stream is a row. Inbound, when it arrives, is a producer into an existing queue
rather than a new mechanism.

# ── the axes ──────────────────────────────────────────────────────────────────────

`accepts`   which unit categories this queue takes. Routing is on `unit_category`, which is
            already a BinKey component, so a unit's queue is decided by what it IS and never
            by where it might go.
`k_cap`     the assignment policy's ordering tolerance, in units. 1 = strict arrival order.
            None means INHERIT the manager's `putaway_window`, not "unbounded" -- so a queue
            states a tolerance only when it has a reason to differ, and the manager-wide
            knob keeps working. See Inventory_Manager._serve_order.
`staging`   how many items may wait at once. None = unbounded. A full queue REFUSES
            admission and the producer holds; it never drops. This is the modelling of
            floor space, and it is what makes pallet FIFO physical rather than stipulated.
`policy`    which waiting item to work next, from `put_policy.PUT_POLICIES`. Where `k_cap`
            narrows the placement pool's freedom, this REPLACES its opinion with the floor's
            own — "finish one SKU before starting the next" is a real rule that no placement
            objective expresses. The two compose.
`crew`      who does the work — role, mode and speeds. Carried here so a queue's throughput
            is a property of the queue and not a global.

# ── what is deliberately NOT here ─────────────────────────────────────────────────

Reach height. A store singleton bin can sit at y_phys 1416 (SINGLETON_BIN_HEIGHT 48, aisles
up to 1440 inches), so "singleton ⇒ cart ⇒ walking speed" charges a walk for something 118
feet up. The honest discriminator is a BIN property known only AFTER assignment, while the
queue is chosen BEFORE it. Splitting the store singleton family by aisle height at build
time — which is what fulfillment already does with FULFILLMENT_AISLE_HEIGHT — would fix it
properly. Until then `accepts` on `unit_category` is a PROXY, and this paragraph is the
record that it is one.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from Warehouse.inventory.inventory_common import PutawayItem
from Warehouse.inventory.put_policy import PUT_POLICIES

#: Unit categories, as `binkey_of(unit)[3]` reports them.
PALLET = 'pallet'
SINGLETON = 'singleton'
FULFILLMENT = 'fulfillment'

#: `accepts` value meaning "every category, including ones added later". A queue set built
#: from a single ANY spec is exactly the pre-queue-set manager, which is what makes the
#: default provably a no-op.
ANY = ('*',)


@dataclass(frozen=True)
class PutQueueSpec:
    """The configuration of one put-away queue. Frozen: a queue's identity must not drift
    mid-run, and a sweep that varies these needs to be able to hash them."""

    name: str
    accepts: tuple[str, ...] = ANY
    #: Ordering tolerance handed to the drain, in units. None = inherit the manager's
    #: `putaway_window` (see the module docstring); 1 = strict arrival order.
    k_cap: int | None = None
    #: Maximum items held. None = unbounded.
    staging: int | None = None
    #: Which waiting item to work next -- a key in `put_policy.PUT_POLICIES`.  None or
    #: 'inherit' means the placement pool's own precedence stands, which is the default.
    #: Composes with `k_cap`: the policy proposes an order, `k_cap` bounds how far that
    #: order may depart from arrival order.
    policy: str | None = None
    #: The crew that works this queue. None = the manager's single put crew, i.e. today.
    crew: object | None = field(default=None, compare=False)

    def __post_init__(self):
        if not self.name:
            raise ValueError('a put queue needs a name — it is the DB discriminator')
        if not self.accepts:
            raise ValueError(f'{self.name}: accepts is empty, so nothing can ever enter it')
        if self.k_cap is not None and self.k_cap < 1:
            raise ValueError(f'{self.name}: k_cap must be >= 1 or None, got {self.k_cap!r}')
        if self.staging is not None and self.staging < 1:
            raise ValueError(f'{self.name}: staging must be >= 1 or None, '
                             f'got {self.staging!r}')
        # Validated HERE, at construction, so a typo in a swept configuration fails when
        # the sweep is defined rather than producing a run that looks like a legitimate arm.
        if self.policy is not None and self.policy not in PUT_POLICIES:
            raise ValueError(f'{self.name}: unknown put policy {self.policy!r}; '
                             f'known: {sorted(PUT_POLICIES)}')

    def takes(self, category: str | None) -> bool:
        return '*' in self.accepts or category in self.accepts


class PutQueue:
    """One spec plus the items waiting under it."""

    __slots__ = ('spec', 'items', 'admitted', 'placed', 'blocked')

    def __init__(self, spec: PutQueueSpec):
        self.spec = spec
        self.items: deque[PutawayItem] = deque()
        # Per-batch counters, reset by `drain_counters`. `blocked` is the one that cannot be
        # derived afterwards: a refused admission leaves no trace anywhere else, so if it is
        # not counted here the backpressure is invisible.
        self.admitted = 0
        self.placed = 0
        self.blocked = 0

    def __len__(self):
        return len(self.items)

    def __repr__(self):
        return f'PutQueue({self.spec.name!r}, {len(self.items)} waiting)'

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def has_room(self) -> bool:
        return self.spec.staging is None or len(self.items) < self.spec.staging

    @property
    def oldest_age(self) -> int | None:
        """The arrival stamp of the longest-waiting item, or None when empty.

        The head is the oldest because every re-entry path preserves order: the rescues
        `appendleft` children back to the head their parent vacated, and the drain requeues
        what it popped. Pinned by Tests/integration/test_putaway_age.py.
        """
        return self.items[0].age if self.items else None

    def admit(self, item: PutawayItem) -> bool:
        """Take one item if there is room. False means the producer must hold it.

        Refusing rather than dropping is the whole point: the backpressure has to reach
        whatever is upstream, because "inbound packs faster than put-away absorbs" is the
        condition being modelled, and a silent drop would make it look like it never
        happened.
        """
        if not self.has_room:
            self.blocked += 1
            return False
        self.items.append(item)
        self.admitted += 1
        return True

    def drain_counters(self) -> dict:
        """Hand over this batch's counters and reset. Depth and oldest age are read at the
        moment of the snapshot and are NOT reset — they are levels, not flows."""
        out = {'queue': self.spec.name, 'depth': len(self.items),
               'oldest_age': self.oldest_age, 'staging': self.spec.staging,
               'admitted': self.admitted, 'placed': self.placed, 'blocked': self.blocked}
        self.admitted = self.placed = self.blocked = 0
        return out


class PutQueueSet:
    """The queues a manager owns, and the routing between them."""

    __slots__ = ('queues', '_by_name')

    def __init__(self, specs):
        specs = list(specs)
        if not specs:
            raise ValueError('a manager needs at least one put-away queue')
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ValueError(f'duplicate put queue name(s) in {names}')
        self.queues = [PutQueue(s) for s in specs]
        self._by_name = {q.name: q for q in self.queues}

    def __iter__(self):
        return iter(self.queues)

    def __len__(self):
        return len(self.queues)

    def __getitem__(self, name: str) -> PutQueue:
        return self._by_name[name]

    @property
    def is_single(self) -> bool:
        """One queue that takes everything — the default, and byte-identically the manager
        as it behaved before queues existed."""
        return len(self.queues) == 1 and '*' in self.queues[0].spec.accepts

    @property
    def depth(self) -> int:
        return sum(len(q) for q in self.queues)

    def route(self, unit) -> PutQueue:
        """The queue this unit belongs in, by `unit_category`.

        FIRST match wins, so spec order is precedence and a catch-all belongs last. A unit
        no queue accepts is an error and not a silent drop: it would otherwise vanish from
        the conservation ledger with nothing to point at.
        """
        cat = getattr(unit, 'unit_category', None)
        for q in self.queues:
            if q.spec.takes(cat):
                return q
        raise LookupError(
            f'no put-away queue accepts unit_category {cat!r}; queues are '
            f'{[(q.name, q.spec.accepts) for q in self.queues]}')

    def snapshot(self) -> list:
        return [q.drain_counters() for q in self.queues]


def single_queue() -> PutQueueSet:
    """The default: one unbounded queue that takes everything, with the policy's full
    ordering freedom. Reproduces the pre-queue-set manager exactly."""
    return PutQueueSet([PutQueueSpec('all')])


def store_and_fulfillment(cart_crew=None, pallet_crew=None, ff_crew=None,
                          pallet_staging: int | None = None,
                          cart_staging: int | None = None,
                          ff_staging: int | None = None) -> PutQueueSet:
    """The three-stream default: singletons into carts, pallets onto a forklift,
    fulfillment on its own.

    `k_cap=1` on the pallet queue is the modelling claim, not a tuning knob: with almost no
    staging floor there is nowhere to lay pallets out and re-sort them, so they are put away
    in arrival order whatever the assignment policy would prefer. The other two keep the
    policy's freedom until someone measures a reason to narrow it.

    Ordered singleton, pallet, fulfillment — specific before general, though none of these
    three is a catch-all, so a category outside the list raises rather than falling through.
    """
    return PutQueueSet([
        PutQueueSpec('store_cart', accepts=(SINGLETON,), crew=cart_crew,
                     staging=cart_staging),
        PutQueueSpec('store_pallet', accepts=(PALLET,), crew=pallet_crew, k_cap=1,
                     staging=pallet_staging),
        PutQueueSpec('fulfillment', accepts=(FULFILLMENT,), crew=ff_crew,
                     staging=ff_staging),
    ])
