"""dock.py — the receiving crew and what is standing on their floor.

# ── why this is not a fourth PutQueue ─────────────────────────────────────────────

`put_queue.py` says, in writing, that a fourth stream should be a row rather than a class.
For put-away streams that is right and this file does not change it. A receiving CREW is a
different thing, and three properties of `PutQueueSet` make membership actively wrong — each
verified in the code, not argued from taste:

  1. `Inventory_Manager._stock`'s drain loop iterates EVERY queue in `self.put_queues` and
     hands its contents to the placement pool. A dock in that set would have its trailers
     placed into bins on batch 1, which is the entire feature undone.
  2. `_cost_putaway` recomputes `self._put_clock = max(x.finish for x in self._put_queues)`,
     and that feeds `_put_base = max(bs.batch_start_time, put_clock)` — the offset applied to
     every put row in every later batch. A dock in the set would silently shift the whole
     put-away stream along the absolute axis with its durations unchanged.
  3. A fourth member flips `len(self.put_queues) == 1` false, which swaps `_stock_queue` from
     a bare deque to `_MultiQueueView` — whose setter raises, and which re-sorts the whole
     backlog by age on every read. Three consumers outside this package read it.

So the dock stands alone and shares the part that genuinely IS the same: the crew's clock,
via `Warehouse.kernel.crew_clock`. There is exactly one copy of the whistle rule.

# ── what the dock does and does not model ─────────────────────────────────────────

It holds `PutawayItem`s — ALREADY PACKED and ALREADY STAMPED — and hands them to the put
queue one at a time as its crew works through the day. Three consequences worth stating:

**Packing happens at ARRIVAL, not at unload.** The load plan is the shipper's and is fixed
when the trailer is loaded. If the day boundary re-packed a half-unloaded trailer, the tier
mix — and therefore `unit_category`, the queue an item routes to, and which bins are legal —
would become a function of crew size and day length, and a receiving-staffing sweep would
silently be a packing sweep as well. `Warehouse/operations/inbound.py` measured the effect:
halving deliveries re-packed 5 of the first 12 SKUs, in BOTH directions.

**The age stamp is the arrival's, not the unload's.** Items reach the dock through `_admit`,
which stamps on arrival, so a pallet that waited three batches on the dock is three batches
old when it finally gets floor space. Stamping at unload would make the longest-waiting
merchandise the youngest thing in the warehouse — the priority inversion `_admit`'s docstring
exists to forbid.

**The dock exerts backpressure on nothing, and nothing exerts backpressure on the dock.**
`arrive()` never refuses: there is no floor limit, because a refusal needs somewhere for the
merchandise to go and the only candidate is `_held`, which `_admit_held` retries INSIDE
`_stock` — so held trailer merchandise would be put away for free, bypassing the crew
entirely. The only bound on receiving is the crew's hours. The dock's DEPTH is the report.
(One real coupling is kept: unloading hands the item to the put queue, so if that queue is at
its staging limit the item goes to `_held` — unloading and put-away admission are different
acts, and the unit is legitimately off the trailer either way.)

# ── two limitations that will produce plausible wrong numbers ─────────────────────

**Arrivals are quantized to BATCHES.** `Inventory_Manager.LEAD_TIME_UNIT` is `'batches'`, so
every trailer in a batch arrives at the same instant — the batch epoch — and the crew has no
sub-batch arrival times to schedule against. The direction is knowable: a real day's arrivals
spread out, so the modelled crew faces its whole day's work at once and its makespan reads
LONG. Sizing a receiving crew off this model would therefore over-staff it. Converting lead
time to seconds moves every restock result on every arm and is a change of its own.

**A put row can carry a smaller `t_abs` than the receive row of the same unit.** Both clocks
restart at the batch epoch and the streams are simulated independently and merged, which is
the model `work_events` already declares. The overlap is bounded by one batch's receive
makespan. Widening `_put_base` to include the dock's finish would over-correct — it would
make the first pallet off the truck wait for the last — and the honest fix needs a dock
coordinate and a per-unit handoff instant, neither of which exists.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from Warehouse.kernel import crew_clock


@dataclass(frozen=True)
class DockSpec:
    """The configuration of the receiving dock. Frozen, like `PutQueueSpec`: a crew's
    identity must not drift mid-run, and a sweep varying these has to hash them."""

    #: Identity, and the `work_events` queue discriminator.
    name: str = 'dock'
    #: How many receivers. The ONLY knob that changes a receiving makespan, because size is
    #: the number of clocks -- see `unload.py` on why there is no speed and no mode.
    size: int = 1
    #: Which `PutawayItem.source` values this dock intercepts. `'reorder'` is every trailer
    #: today, because a reorder arrival IS the trailer. The tuple is the extension point: a
    #: vendor-managed delivery or a cross-dock transfer with no lead-queue entry would earn
    #: its own source value and be added here. Intake and the reloader's evictions are
    #: deliberately NOT intercepted -- initial stocking is not a receipt, and a re-slotted
    #: unit never left the building.
    sources: tuple[str, ...] = ('reorder',)

    def __post_init__(self):
        if not self.name:
            raise ValueError('a dock needs a name — it is the work_events discriminator')
        if not self.sources:
            raise ValueError(f'{self.name}: sources is empty, so nothing can ever arrive')


class Dock:
    """One spec, one crew clock, and the merchandise standing on the floor."""

    __slots__ = ('spec', 'items', 'clocks', 'cost', 'records',
                 'unloaded', 'cut', 'seconds', 'deliveries')

    def __init__(self, spec: DockSpec, cost=None):
        self.spec = spec
        self.items: deque = deque()
        # BOUND AT CONSTRUCTION, unlike PutQueue's clocks, which are None until
        # `_bind_put_crews` reaches them. A dock is not a member of `PutQueueSet`, so nothing
        # would ever reach it -- and an unbound clock makes `crew_clock.can_start` return
        # True forever, which ships the feature fully wired and completely inert, with a cut
        # count of zero and no log line. Binding here makes that unreachable.
        self.clocks = crew_clock.new_clocks(spec.size, spec.name)
        self.cost = cost
        #: (t0, dur, sku, qty, worker) per unload, on the BATCH-LOCAL clock. Drained per
        #: batch; the runner adds the epoch.
        self.records: list = []
        # Per-batch FLOWS, reset by `snapshot()`. `depth` is a level and is not among them.
        self.unloaded = 0
        self.cut = 0
        self.seconds = 0.0
        self.deliveries = 0

    def __repr__(self):
        return (f'Dock({self.spec.name!r}, {len(self.items)} waiting, '
                f'{crew_clock.size_of(self.clocks)} crew)')

    # ── what is standing here ─────────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def depth(self) -> int:
        """Storage units on the floor. A LEVEL -- `snapshot()` reports it without resetting.

        The same unit as `Inventory_Manager.queue_depth`, and DISJOINT from it: an item is on
        the dock or in a put queue, never both. A reader wanting the whole unbinned backlog
        sums them.
        """
        return len(self.items)

    @property
    def crew_size(self) -> int:
        return crew_clock.size_of(self.clocks)

    def takes(self, source: str | None) -> bool:
        """Does this dock intercept an admission from `source`?"""
        return source in self.spec.sources

    def arrive(self, item) -> None:
        """Put one already-stamped item on the floor.

        NEVER refuses -- see the module docstring. Appended, so the deque is in arrival order
        and the head is the oldest, which is what makes the unload FIFO and keeps
        `PutawayItem.age` monotonic along it.
        """
        self.items.append(item)

    def note_arrivals(self, plans) -> None:
        """Record that `plans` deliveries reached the dock this batch.

        A COUNT, not the plans themselves: a run is hundreds of batches and a `LoadPlan` pins
        a tuple of live `StorageUnit`s plus a cloned `Order`, which is precisely the leak
        `drain_inbound` turned out to be.
        """
        self.deliveries += len(plans)

    # ── the crew ──────────────────────────────────────────────────────────────────
    def can_start(self, deadline: float | None) -> bool:
        """Is anyone free to BEGIN an unload before `deadline`? A START gate -- see
        `crew_clock.can_start` for what `deadline` is measured against."""
        return crew_clock.can_start(self.clocks, deadline)

    def charge(self, dur: float):
        """Book `dur` to whoever is free earliest; return (start, worker index)."""
        self.seconds += dur
        return crew_clock.charge(self.clocks, dur)

    @property
    def finish(self) -> float:
        """When the last receiver becomes free, on the batch-local clock."""
        return crew_clock.finish(self.clocks)

    def reset_clocks(self) -> None:
        crew_clock.reset(self.clocks)

    # ── handing the batch over ────────────────────────────────────────────────────
    def drain_records(self) -> list:
        """This batch's unload records, and start the crew's clock over.

        A DRAIN IS A BATCH BOUNDARY, for the same reason `drain_putaway_records` is: the
        records carry `t0` on the crew's own clock measured from the start of the batch,
        because the batch epoch is not known here -- receiving runs at the TOP of the batch,
        before the pickers have been simulated. The runner adds the epoch when it writes the
        rows. Resetting is load-bearing, not tidiness: without it the clock accumulates across
        the whole arm while the runner still adds the epoch, and every row after batch 0 is
        stamped too late by the total receiving seconds of every preceding batch.
        """
        recs, self.records = self.records, []
        self.reset_clocks()
        return recs

    def snapshot(self) -> tuple:
        """`(depth, unloaded, cut, seconds)` — and reset the three per-batch counters.

        Two of those three are flows; `cut` is not, though it resets like one.  See
        `PutQueue.cut`, which has the same shape and the same warning: it re-counts the
        whole standing dock every batch, so it must never be summed across them.

        Depth is a LEVEL and survives, exactly as `PutQueue.drain_counters` treats its own.
        Must run once per batch: a second call in the same batch reports zeros, and that is
        not hypothetical — the put side shipped that defect and persisted an idle-looking
        queue for every skipped batch.
        """
        out = (len(self.items), self.unloaded, self.cut, self.seconds)
        self.unloaded = self.cut = 0
        self.seconds = 0.0
        self.deliveries = 0
        return out
