"""leaf_scope — who answers a leaf's inbound and put-away questions: it, or the site.

`_build_leaf` asked `site is None` / `pool is None` twenty-eight times, and every pair was the
same dispatch under a different name:

| the question | solo | site-docked |
|---|---|---|
| the transit census | `mgr.transit_snapshot()` | `coord.transit_census_for(mgr)` |
| the receiving snapshot | `mgr.receiving_snapshot()` | `coord.snapshot_for(mgr)` |
| this batch's receipts | `mgr.drain_receiving_records()` | `coord.drain_records_for(mgr)` |
| the dock depth | `mgr.dock_depth` | `coord.dock_depth_for(mgr)` |
| the yard rows | `mgr.drain_yard_*()` | nothing — the SITE collects them |
| the receiving roster | `_recv_crew.workers(uid)` | `site.workers` |
| the put roster | `_put_crew.workers(uid)` | `pool.workers` |

Two adapters already existed — "the leaf's own manager answers" and "the coordinator answers on
its behalf" — with no interface between them, so the dispatch was re-decided at every call site
and correctness was held up by REFUSALS IN THE DOMAIN LAYER. Eight accessors under `Warehouse/`
and `Inbound/` raise under a site scope, which means `Warehouse/` had to learn that a DRIVER
concept exists. Those refusals are still there and still right, but they are a backstop now
rather than the mechanism.

## The three parameters are a LADDER, not eight combinations

The ticket that asked for this counted `pool`, `site_gain` and `site` as three independent
`None`-able parameters — eight nominal combinations, "~3 legal, enforced by guards scattered
across `_build_site_dock`, `_build_put_pool` and `_bind_put_crews`". Reading those guards, the
reason only three are legal is stronger than scattering: **they form a ladder.**

    SOLO      no pool, no site      an uncoupled run
      |       `_build_put_pool` is only called on a coupled unit
    POOLED    pool, no site         a coupled unit with inbound OFF (site-dock 06)
      |       `_build_site_dock` reads `pool.workers[-1].uid`, so it cannot exist without one
    DOCKED    pool and site         a coupled unit with the standing yard

`_build_put_pool` never returns `None` — it raises or it builds — so `site` implies `pool`
implies coupled. The fourth corner (a site dock with no put pool) is not one of eight
combinations that happens to be guarded: it is unreachable, and here it is UNNAMEABLE, because
there is no class for it. `scope_for` is the only constructor and it walks the ladder.

`site_gain` is not on this ladder at all. It is read at exactly one place — binding the gain
bundle into the transit — so it stays an argument to `_build_arm` rather than becoming a fourth
rung; a scope member consulted once is a parameter wearing a costume.

## What each rung overrides

`PooledScope` changes the PUT half only; a coupled unit with inbound off still answers every
inbound question out of its own manager, byte-identically to a solo run. `SiteScope` adds the
inbound half on top. That is why this is an inheritance chain rather than three siblings: each
rung is the one below it with one more group of answers moved to the site, and writing it any
other way would duplicate the put half into two classes.

## The instants are preserved, including where the two poles disagree

`lead_depth` and `in_transit` are read at DIFFERENT MOMENTS by the two poles, and that is kept
exactly. The site takes all three transit numbers off one partitioning pass before
`check_reorders` fires; a solo leaf reads them off the manager afterwards, so its values include
this batch's own reorders and the site's do not. That asymmetry predates this module (see
`transit_census`' own note) and moving either read would move published numbers, so `lead_depth`
and `in_transit` stay METHODS taking the census value rather than becoming fields resolved at
census time, which is the shape that would silently align them.
"""
from __future__ import annotations

__all__ = ['LeafScope', 'PooledScope', 'SiteScope', 'scope_for']


class LeafScope:
    """SOLO — the leaf's own manager answers every question, and it owns its put crew.

    The base is a real rung, not an abstract interface: an uncoupled run uses this class
    unchanged, so every method here is the production answer rather than a stub to override.
    """

    __slots__ = ()

    #: Does this leaf run its own `check_reorders` composition? False only when the SITE drives
    #: phases 0-5 for every leaf at once, which is the one thing a leaf cannot do for itself.
    drives_own_composition = True

    #: Does `drain_putaway_records` reset the crew clocks? The pool owns the reset when the
    #: clock list is shared, because `reset` mutates in place and one list cannot carry two.
    reset_put_clocks = True

    def __repr__(self) -> str:
        return f'<{type(self).__name__}>'

    # ── construction: what the leaf takes instead of building ─────────────────────────
    # Each of these is `None` on the rung that builds its own, so the call site reads
    # "take the site's, or build one" rather than re-deciding what a site is.

    workers = None          #: the RECEIVING roster; None -> this leaf mints its own
    dock = None             #: the site's dock; None -> this leaf builds one
    transit = None          #: the site's yard; None -> this leaf builds one
    coord = None            #: the site's receiving coordinator; None -> this leaf builds one
    put_workers = None      #: the pooled put roster; None -> this leaf mints its own
    put_clocks = None       #: the pooled put clock list; None -> this leaf's own

    def put_uid_after(self, uid: int, put_crews: dict, first_queue: str) -> int:
        """Where the uid cursor sits after the put crews are minted.

        Solo, the per-queue loop already advanced it. Pooled, it must jump to the POOL's block
        end -- the pool's block starts above BOTH channels' dense picker uids, so a cursor left
        at this leaf's own `k_pickers + put_size` would put the smaller leaf's receivers inside
        the putters' block and merge two crews in one DB.
        """
        return uid

    def bind_put(self, mgr, channel_name: str) -> None:
        """Nothing to bind: this leaf's manager owns its own put-away."""

    def bind_receiving(self, mgr, channel_regime) -> None:
        """Nothing to bind: this leaf's manager owns its own dock."""

    # ── the batch: inbound ────────────────────────────────────────────────────────────

    def transit_census(self, mgr):
        """`(rows, in_transit_qty, lead_queue_depth)` — the second and third are None here.

        A solo leaf reads those two off the manager LATER, after `check_reorders` has fired
        this batch's reorders, so they cannot be resolved at census time without moving them.
        See the module docstring: the two poles genuinely read at different instants and this
        module preserves that rather than tidying it.
        """
        return mgr.transit_snapshot(), None, None

    def lead_depth(self, mgr, census_depth):
        return mgr.lead_queue_depth

    def in_transit(self, mgr, census_qty):
        return mgr.in_transit_qty

    def receiving_snapshot(self, mgr):
        return mgr.receiving_snapshot()

    def dock_depth(self, mgr):
        return mgr.dock_depth

    def yard_rows(self, mgr, batch_id: int):
        """`(trailer stamps, drain levels)` for THIS leaf to record.

        Site-docked this is empty and the site collects instead: a trailer's load is mixed by
        construction and a door is occupied by the trailer rather than by either channel's share
        of it (ADR-0005). Both leaf accessors REFUSE under a site scope, so the empty pair here
        is the reason nothing calls them, not a duplicate of the refusal.
        """
        return mgr.drain_yard_trailers(), [(batch_id, *lv) for lv in mgr.drain_yard_drains()]

    def standing_yard(self, mgr):
        """The censored tail at run end. `standing_yard_trailers` does not drain, so two leaves
        reading it would bill every standing trailer twice -- which is why it refuses under a
        site scope and why this returns nothing there."""
        return mgr.standing_yard_trailers()

    def recv_drain(self, mgr, *, arm_clock, recv_clock, day):
        """`(receipts, repacks, base)` for this batch.

        Both streams must be taken in the SAME batch or the one left behind lands against the
        next batch's epoch. `drain_repacks` deliberately does not reset the crew clocks and both
        streams' `t0` were stamped when the work happened, so their order does not matter.
        """
        return (mgr.drain_receiving_records(),
                mgr.drain_repack_records(),
                max(arm_clock, recv_clock))

    def note_recv(self, mgr, recv_clock) -> None:
        """Nothing to report: an unpooled leaf's `drain_receiving_records` already reset its own
        crew clock."""

    # ── the batch: put-away ───────────────────────────────────────────────────────────

    def put_window(self, *, day, day_end, arm_clock, put_clock):
        """`(base, deadline)` — the epoch put-away is charged from, and its whistle.

        Solo there is no base (the manager keeps its own clock) and the whistle is what is left
        of THIS leaf's day.
        """
        return None, (None if day_end is None else day_end - max(arm_clock, put_clock))

    def put_clock_for(self, put_clock, put_base):
        """Which clock a caller that must name ONE epoch should use.

        Two callers want this and they want the same answer: the skipped-batch close-out, and
        the `_put_recs` empty case where a pooled leaf still owes the pool a report. Solo the
        answer is the leaf's own clock, which makes the second one an assignment to itself --
        deliberately, because "nothing changes here" is the solo behaviour being preserved.
        """
        return put_clock

    def put_base_for_batch(self, site_base, batch_start, put_clock):
        """The epoch this batch's put rows are stamped from.

        Solo the crew picks this wave's queue up when the wave is RELEASED or when it finished
        the last one, whichever is later. Pooled it was already fixed when the site day opened,
        so the value handed in is the answer and this must not recompute it.
        """
        return max(batch_start, put_clock)

    def note_put(self, mgr, put_clock) -> None:
        """Nothing to report: this leaf's clocks are its own."""


class PooledScope(LeafScope):
    """COUPLED, INBOUND OFF — one site put crew, but every inbound question is still the leaf's.

    The middle rung, and a real configuration rather than a degenerate one: the funnel runs the
    coupled inbound-off pole in every cell (site-dock 06). Nothing inbound is overridden here, so
    such a run answers `transit_census`, `receiving_snapshot` and the rest exactly as a solo run
    does -- which is the byte-identity that pole is supposed to have, now by construction.
    """

    __slots__ = ('_pool',)

    reset_put_clocks = False

    def __init__(self, pool) -> None:
        self._pool = pool

    def __repr__(self) -> str:
        return f'<{type(self).__name__} pool={self._pool!r}>'

    @property
    def put_workers(self):
        return self._pool.workers

    @property
    def put_clocks(self):
        """The POOL's list, shared by identity: `crew_clock.charge` books every put to whichever
        putter is free earliest across BOTH channels, and `reset` mutates in place -- which is
        why the pool, and not either manager, owns the reset (`reset_put_clocks`)."""
        return self._pool.clocks

    def put_uid_after(self, uid: int, put_crews: dict, first_queue: str) -> int:
        return put_crews[first_queue][-1].uid + 1

    def bind_put(self, mgr, channel_name: str) -> None:
        """Both ways: the manager routes phase 5 to the pool, and the pool learns which channel
        this leaf is so it can hand it its share of the day."""
        mgr.putaway_pool = self._pool
        self._pool.bind(mgr, channel_name)

    def put_window(self, *, day, day_end, arm_clock, put_clock):
        """BOTH NUMBERS ARE THE SITE'S and neither is this leaf's to compute. One shared clock
        list cannot carry two epochs, so the base is the site day's start and the whistle is what
        is left of THAT day -- never `arm_clock`, which would idle the site's putters whenever
        EITHER pick crew overran its day and would misattribute a picking overrun to put-away's
        cut. `open_batch` is idempotent per day, so both leaves get the same answer."""
        return self._pool.open_batch(day)

    def put_clock_for(self, put_clock, put_base):
        return put_base

    def put_base_for_batch(self, site_base, batch_start, put_clock):
        return site_base

    def note_put(self, mgr, put_clock) -> None:
        self._pool.note_records(mgr, put_clock)


class SiteScope(PooledScope):
    """COUPLED, STANDING YARD — the coordinator answers for inbound as well as put-away.

    The top rung. It extends `PooledScope` rather than `LeafScope` because a site dock cannot
    exist without a put pool (`_build_site_dock` reads `pool.workers[-1].uid`), so "site-docked
    but unpooled" is not a state this class can be in.
    """

    __slots__ = ('_site',)

    drives_own_composition = False

    def __init__(self, pool, site) -> None:
        super().__init__(pool)
        self._site = site

    def __repr__(self) -> str:
        return f'<{type(self).__name__} site={self._site!r}>'

    @property
    def workers(self):
        return self._site.workers

    @property
    def dock(self):
        """ONE dock, built above both leaves with a per-regime PRICE LIST (site-dock 27): the
        unload price is a statement about the merchandise, so the site charges each channel's own
        constant."""
        return self._site.dock

    @property
    def transit(self):
        """ONE site is ONE yard. Two transits behind one dock would give each leaf its own
        trailers while the drain ranked only the coordinator's, and `_advance_lead_queue` would
        tick each channel's copy of the SUPPLIER lead queue -- an order placed with a 3-batch
        lead arriving in 2, on every coupled run, silently."""
        return self._site.transit

    @property
    def coord(self):
        return self._site.coord

    def bind_receiving(self, mgr, channel_regime) -> None:
        """The SECOND bind, which stamps `site_scoped` on the leaf and turns the six leaf
        accessors into refusals. It must run after the transit is set and after intake has filled
        `_originals`: `bind` reads this leaf's whole catalogue partition to build the
        `{sku: leaf}` owner dict, and asserts the leaf holds the coordinator's own transit."""
        self._site.coord.bind(mgr, channel_regime)

    # ── inbound: the coordinator answers on this leaf's behalf ────────────────────────

    def transit_census(self, mgr):
        """THE CENSUS IS THE SITE'S and this leaf takes its own share. All three reads come off
        ONE pass so they cannot disagree, and the split is by SKU -- the pack rule (ADR-0005) --
        so the rows and the units sum back to the site's own census exactly."""
        return self._site.coord.transit_census_for(mgr)

    def lead_depth(self, mgr, census_depth):
        return census_depth

    def in_transit(self, mgr, census_qty):
        return census_qty

    def receiving_snapshot(self, mgr):
        """`snapshot_for` partitions once and serves each leaf once. The leaf accessor refuses
        here because three of its four values RESET, so the first caller would take the site's
        whole batch and leave the other channel reporting an idle dock."""
        return self._site.coord.snapshot_for(mgr)

    def dock_depth(self, mgr):
        return self._site.coord.dock_depth_for(mgr)

    def yard_rows(self, mgr, batch_id: int):
        return [], []

    def standing_yard(self, mgr):
        return []

    def recv_drain(self, mgr, *, arm_clock, recv_clock, day):
        """BOTH STREAMS COME OFF THE SITE'S ONE DRAIN, partitioned by the owner of each row's
        sku -- the same `{sku: leaf}` dict step 1 packs by, so an unload row, a repack row and
        the lot that produced them cannot land in different channels.

        And the BASE is the SITE DAY's, not this leaf's `arm_clock`: one crew on one dock has one
        epoch, and basing it on a pick crew's release would idle the site's receivers whenever
        EITHER channel overran its day. `open_batch` is idempotent per day, so this reads the
        value the drain was already run against rather than computing a second one.
        """
        base, _ = self._site.coord.open_batch(day)
        return (self._site.coord.drain_records_for(mgr),
                self._site.coord.drain_repacks_for(mgr),
                base)

    def note_recv(self, mgr, recv_clock) -> None:
        """THE SITE CARRY IS COMMITTED ONCE PER SITE DAY, after EVERY leaf has stamped -- and so
        is the shared crew's clock reset, which `note_records` owns because
        `drain_receiving_records` (its uncoupled owner) refuses here. `None` when this leaf
        recorded nothing: the crew is where it was, exactly as an unpooled leaf leaves
        `recv_clock` alone."""
        self._site.coord.note_records(mgr, recv_clock)


def scope_for(pool, site) -> LeafScope:
    """The ONE constructor, and the only place the ladder is walked.

    A site dock with no put pool is refused rather than silently degraded: `_build_site_dock`
    reads `pool.workers[-1].uid` to mint the receiving block, so this state cannot arise from the
    builders -- and if it ever does, the symptom without this check is a receiving crew whose
    uids sit inside the putters' block, which merges two crews in one DB and no table says so.
    """
    if site is not None:
        if pool is None:
            raise ValueError(
                'a leaf scope was asked for a site dock with no put pool. The site\'s receiving '
                'uid block chains off the POOL\'s block end, so there is no correct roster to '
                'build here; every coupled unit has a pool (`_build_put_pool` raises rather '
                'than returning None), so this is a wiring error, not a configuration.')
        return SiteScope(pool, site)
    if pool is not None:
        return PooledScope(pool)
    return LeafScope()
