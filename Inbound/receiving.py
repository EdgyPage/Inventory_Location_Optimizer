"""receiving — the site's receiving coordinator: one dock, one yard, N leaves.

`SiteReceiving` owns every DECISION of a standing drain and holds no merchandise.  It
sits ABOVE the inventory managers rather than inside one, which is what lets one dock
serve two channels without either manager knowing the other exists.

# ── why it lives here, and cannot live anywhere else ──────────────────────────────

`wh_operations -> wh_inventory` is forbidden (`context/architecture.yml:88`), so the
package where crews live cannot drive a manager's drain; `warehouse_core -> inbound` and
`wh_inventory -> inbound` are forbidden in BOTH directions (`:102-103`).  `Inbound/` is
therefore the only package that may sit above two managers, which is why the put pool
lands here too.

The consequence is that this module can never import a manager.  It DUCK-TYPES its
leaves and reaches each one through exactly three public ports:

    leaf.plan_lot(sku, qty, source) -> (plans, items)
        Step 1's per-lot body: resolve `_originals`, apply `inbound_split`, pack, stamp,
        and debit the pack shortfall against the remainder ledger.
    leaf.accept(item, dur) -> None
        Step 4's per-unit body: the deferred->queued flip, `_recv_seconds`, `_queue`.
    leaf.owned_skus() -> iterable[int]
        The catalogue partition this leaf is responsible for.  Read ONCE, at bind time,
        to build the `{sku: leaf}` owner dict — never during a drain.

Everything else a drain touches is the coordinator's own: the `Dock`, the `YardTransit`,
the ctx freeze, the door fill, the unload and the handoff ORDER.

HOW A MIXED TRAILER FINDS ITS WAY HOME — two routes, because they answer two different
questions:

  * STEP 1 holds a bare `(sku, qty)` lot; no unit exists yet, so it resolves the owner
    from the `{sku: leaf}` dict built at bind time.  An overlapping sku is REFUSED there:
    it would mean the channel filter let one order into both leaves, and the symptom
    would be merchandise silently delivered to the wrong warehouse.
  * STEP 4 holds a `PutawayItem`, so it asks `regime_of(item.unit)` (wh_kernel,
    dependency-free, single-valued per entity).  No owner field is carried, stamped or
    mapped at the unit level — `regime_of` already answers it.

The two are CROSS-CHECKED at the handoff.  A unit whose regime names one leaf while the
owner dict names another is the only way the catalogue partition and the regime tagging
can disagree, and a run that routes by one while the other is true is a run whose ledgers
balance in the wrong warehouse.

# ── what is NOT here ──────────────────────────────────────────────────────────────

The DRIVER wiring of a two-leaf standing run.  Three things a live coupled run needs are
named out of scope by this build's ticket, and each is somebody else's decision: the
site-scoped yard rows (`yard_trailers`, `yard_drains`) have no declared artifact until
`<pair>/_site/` lands with its contract bump ("Design the site scope in the run tree",
ADR-0005); the shared transit carries ONE `gain_bundle` slot for two owners ("Design the
composite gain bundle"); and one dock has one `UnloadCost` while the two channels price at
two ("One unload price for the site dock", the site-dock map's open question).  So the
coordinator is complete and the run that fields it is the next ticket's — the same
seam-before-consumer order this map has used throughout.  `site_rows` below is where a
coupled drain's yard rows wait for that artifact, and `drain_site_rows` is its accessor.

Within-day interleaving.  One batch is one site day and the drain stays drain-quantized;
an event-driven cadence is out of scope, inherited.
"""
from __future__ import annotations

from collections import deque

from Warehouse.kernel.allocation import partition
from Warehouse.kernel.regime import REGIMES, regime_of

from Inbound.site_space import compose_site_view


#: THE SITE COMPOSITION, as `(scope, phase)` pairs — the second canonical sequence beside
#: `check_reorders`' own.  `Tests/unit/test_reorder_phases.py` pins both, and pins that the
#: phase NAMES here are that list unchanged: the site interleave is a claim about the SAME
#: seven phases, so a phase added to one composition and not the other fails there.
#:
#: WHICH PHASES ARE THE SITE'S.  `_advance_lead_queue` delegates to `transit.advance()` and
#: the transit is the SITE's one yard, so two leaves ticking it would decrement every
#: supplier lead TWICE — an order placed with a 3-batch lead would arrive in 2, and nothing
#: would raise.  ("Design the site receiving coordinator" section 4 put phases 0-3 on every
#: leaf; phase 1 is the exception, and it is the code rather than the design that found it.)
#: The receive is the site's because there is one dock.  WHICH leaf a site phase is driven
#: on is not a choice: `bind` asserts every leaf holds the coordinator's own transit, so it
#: is the same call either way.
#:
#: PHASE-MAJOR — every leaf runs a phase before any leaf runs the next — and that is
#: load-bearing rather than cosmetic.  `_fire_reorders` loads fired orders onto the ONE open
#: trailer and `_release_arrivals` departs it; run leaf-major (leaf A fires AND releases,
#: then leaf B) and every trailer carries one channel's merchandise.  The charter's mixed
#: load would never happen, the site dock would be two docks wearing one name, and no table
#: would say so.
#: How far two leaves' drain epochs may differ and still be "one instant".  Floats
#: compare with a tolerance and never with `==` (CLAUDE.md section 2): the driver hands both
#: leaves the SAME site epoch, so any gap at all is a defect, but a value that has been
#: through an addition and back can differ by an ulp on a clock measured in millions of
#: seconds.  Absolute, and deliberately far below anything a dock decision can see.
_EPOCH_TOL: float = 1e-6

#: How far the leaves' own accrued receiving seconds may sit from the dock's, and still be
#: the same labour.  The two sums are the same `dur` values added in different orders, so
#: the gap is float re-association over one batch and nothing else; a real leak is a whole
#: unload (seconds, not nanoseconds).  Floats compare with a tolerance (CLAUDE.md section 2).
_SECONDS_TOL: float = 1e-6

SITE_PHASES: tuple = (
    ('leaf', '_tick_batch'),
    ('leaf', 'reclaim_emptied_bins'),
    ('site', '_advance_lead_queue'),
    ('leaf', '_fire_reorders'),
    ('leaf', '_release_arrivals'),
    ('site', '_receive'),
    ('leaf', 'drain_putaway'),
)


class SiteReceiving:
    """The site's dock, yard and drain record, driving N leaves through three ports.

    Constructed by the driver where the `Dock` and the `YardTransit` are built, and bound
    onto each leaf it serves (`mgr.receiving`) — the same injection precedent as
    `enable_receiving` and `mgr.packer`: the broker holds what it is handed, and nothing
    under `Warehouse/` imports this package.

    A COUPLED RUN REQUIRES THE STANDING YARD, and this refuses a transit without it at
    CONSTRUCTION.  Of the three transits only `YardTransit` carries a yard, doors and a
    dock ranking; the v1 `TrailerTransit` and the flag-off `BatchTransit` drain through the
    manager's own dock-deque branch, which is a separate ~20-line path with its own owner
    problem.  Building owner routing twice, in two unrelated drains, for a mode no
    experiment runs is work with no reader — and the alternative to refusing is a SILENT
    fallback to per-leaf receiving, which is a run that looks healthy and answers a
    different question (memory `pool-run-swallows-dead-arms`).

    `day` is the SITE working day, and it is what the site receiving clock is based on.
    None is every run without a whistle grid, and then `open_batch` refuses rather than
    inventing one — the same precondition `Inbound/putaway_pool.py` states for the put
    crew, for the same reason: coupling is an era feature and the era completes the grid.
    """

    def __init__(self, dock, transit, *, day=None):
        if not getattr(transit, 'STANDING', False):
            raise ValueError(
                f'a site receiving coordinator drains a STANDING yard, and {transit!r} '
                f'carries none. The v1 and flag-off transits drain through the manager own '
                f'dock deque, which is a different path with its own owner routing; a '
                f'coordinator over one of them would either build that routing twice or '
                f'fall back to per-leaf receiving with a coupled label on it')
        #: The one dock: crew clocks, the unload cost model, and the `records` /
        #: `unloaded` / `seconds` / `cut` counters the run reports from.
        self.dock = dock
        #: The one yard: trailer, yard and door STATE.  This object owns the decisions
        #: that state is consulted for.
        self.transit = transit
        #: The site working day — `start_of(i)` / `remaining(base)`.  See `open_batch`.
        self.day = day
        #: THE SITE RECEIVING CARRY, on the absolute axis: where the one receiving crew
        #: finished.  Committed once per site day, after every leaf has reported, so both
        #: leaves of one batch read the same base ("one base, asked for twice").
        self.recv_clock: float = 0.0
        #: A coupled drain's yard rows, held at SITE scope because that is what they are.
        #: Nothing writes them yet — see the module note on `<pair>/_site/`.  THE DRIVER
        #: THAT FIELDS A COUPLED RUN MUST DRAIN THIS EVERY BATCH (`drain_site_rows`), the
        #: way it drains every other per-batch row source: a list that only grows is the
        #: only evidence a coupled drain happened, and compounding it would hand whatever
        #: finally reads it every batch's rows at once.
        self.site_rows: list = []
        #: THE SITE DOCK'S OWN PER-SITE-DAY TOTALS, `(depth, unloaded, cut, seconds)` per
        #: partitioned day — the `site_receiving` table's rows (site-dock 15 section 7).
        #: Parked here for the same reason `site_rows` is: they are the SITE's counters,
        #: accrued on one dock by one crew, so a copy on either leaf would be the site total
        #: wearing one channel's name.  DRAINED EVERY BATCH (`drain_site_totals`) by the
        #: driver that fields a coupled run, exactly like `site_rows`.
        #:
        #: These are the OTHER SIDE of the partition below, not a summary of it: the shares
        #: are accrued per channel (`_unloaded`, `_cut`, each leaf's own `receiving_seconds`)
        #: and these come off the dock's own counters.  That is what makes
        #: `receiving_report.reconcile_pair`'s site-total closure evidence rather than a
        #: restatement of the shares.
        self.site_totals: list = []
        self._leaves: dict = {}          # channel -> leaf, in bind order
        self._owner: dict = {}           # sku -> leaf  (step 1's route)
        # The site day's state.  `_open` is None until the first `open_batch`.
        self._open = None
        self._base = 0.0
        self._deadline = None
        self._owed_records: set = set()
        self._finish = None
        #: THE DECOMPOSABLE HALF of the dock's per-batch flows, accrued per channel at the
        #: handoff — where the owner is already resolved — because the dock counts one
        #: site total and ADR-0005 puts pack-denominated receiving back on the owning
        #: channel.  Reset with the site totals in `_partition`.
        self._unloaded: dict = {}
        self._cut: dict = {}
        #: Each leaf's own `receiving_seconds` when it was last snapshotted.  The per-leaf
        #: SECONDS are read as this delta rather than accrued here, and that is deliberate:
        #: a repack is charged on the leaf (`_charge_repack`) and never passes through
        #: `receive`, so a coordinator-side sum would silently omit exactly the rows
        #: `receiving_report` check 5 exists for.  "Each leaf's `batch_stats` scalars come
        #: from its own `_recv_seconds`" is the precondition site-dock 15 named.
        self._recv_seen: dict = {}
        #: The site day's partition of the dock: computed ONCE and served to each leaf
        #: ONCE.  `None` until the first `snapshot_for`/`drain_*_for` of the day.
        self._share = None

    def __repr__(self):
        return (f'SiteReceiving(crew={self.dock.crew_size}, '
                f'channels={tuple(self._leaves)!r}, skus={len(self._owner)})')

    # ── binding, and the owner dict ───────────────────────────────────────────────

    @property
    def leaves(self) -> tuple:
        """The bound leaves in BIND ORDER, which is the declared channel order.

        The order is the model: `SITE_PHASES` is phase-major over exactly this sequence,
        and `_fire_reorders` loads the shared open trailer in it, so store-first means a
        mixed trailer's store lots load first.  A tuple, not the dict, because callers
        sequence leaves and never index one by name.
        """
        return tuple(self._leaves.values())

    def bind(self, leaf, channel: str) -> None:
        """Serve `channel`'s leaf.  Call once per leaf, before the batch loop.

        Builds this leaf's half of the `{sku: leaf}` owner dict from its own catalogue
        partition, and REFUSES an overlap.  An sku owned by two leaves means the channel
        filter (`_channel_runs_for`) let one order into both, and the only symptom
        downstream would be merchandise delivered to the wrong warehouse and a ledger that
        balances there — so it is checked at the one moment both partitions are in hand.

        The leaf's transit must BE this coordinator's.  That is what makes `SITE_PHASES`'
        site-scoped `_advance_lead_queue` honest: the phase is driven on one leaf and
        reaches `transit.advance()`, so "which leaf" is not a choice as long as the
        identity holds, and this is where it is established rather than assumed.
        """
        if channel not in REGIMES:
            raise ValueError(
                f'{channel!r} is not a storage regime {REGIMES!r}; the channel a leaf binds '
                f'under IS the regime its units carry, because that is what routes the '
                f'handoff -- and a name outside the set would not fail until the first unit '
                f'came off a trailer, with the doors filled and the crew already charged')
        if channel in self._leaves:
            raise ValueError(
                f'the {channel} leaf is already bound to this coordinator; two leaves of '
                f'one channel would both claim that channel merchandise at the handoff')
        if getattr(leaf, 'transit', None) is not self.transit:
            raise ValueError(
                f'the {channel} leaf holds a different transit than this coordinator; one '
                f'site is one yard, and two yards behind one dock would give each leaf its '
                f'own trailers while the drain ranked only the coordinator ones')
        own = tuple(leaf.owned_skus())
        clash = sorted(s for s in own if s in self._owner)
        if clash:
            raise ValueError(
                f'sku(s) {clash[:8]}{"..." if len(clash) > 8 else ""} are owned by the '
                f'{channel} leaf and by another; the leaves of a site PARTITION one '
                f'catalogue, so an overlap means the channel filter let one order into '
                f'both and a lot of it would be delivered to whichever leaf bound first')
        self._leaves[channel] = leaf
        for sku in own:
            self._owner[sku] = leaf
        # SITE SCOPE BECOMES REAL AT THE SECOND LEAF, and it is stamped on EVERY bound leaf
        # (the first one included, retroactively): from here on a leaf holds the site's dock
        # and the site's yard, so its own per-leaf receiving and transit accessors would
        # answer a site question with one channel name on it.  They refuse instead --
        # `Inventory_Manager.site_scoped`.  A leaf reporting `dock_depth == 0` while the
        # site dock is backed up is exactly the silent-wrong-number class this repo keeps
        # getting bitten by (memories `a-right-site-total-hides-two-wrong-shares`,
        # `free-bins-counts-the-whole-geometry`).
        if len(self._leaves) > 1:
            for lf in self._leaves.values():
                lf.site_scoped = True

    def _owner_of(self, sku: int):
        """Step 1's route: which leaf packs a bare `(sku, qty)` lot.

        Unbound coordinators (every one-leaf run built before a `bind` existed) keep the
        historical behaviour -- the single leaf the drain was handed owns everything -- and
        that fallback lives at the ONE call site rather than here, so this method always
        means "the dict says".
        """
        leaf = self._owner.get(sku)
        if leaf is None:
            raise ValueError(
                f'sku {sku} arrived on a site trailer and no bound leaf owns it; the owner '
                f'dict is built from the leaves own catalogue partitions at bind time, so '
                f'an unowned sku means the yard is carrying merchandise this site never '
                f'ordered')
        return leaf

    def _leaf_for(self, regime: str):
        """Step 4's route: which leaf takes a unit of `regime`."""
        leaf = self._leaves.get(regime)
        if leaf is None:
            raise ValueError(
                f'a unit of the {regime!r} regime unloaded at a site dock serving '
                f'{tuple(self._leaves)!r}; the channel a leaf binds under IS its regime, so '
                f'this unit has nowhere to be accepted and its ledger legs would strand')
        return leaf

    # ── the site day ──────────────────────────────────────────────────────────────

    def open_batch(self, index: int) -> tuple:
        """`(base, deadline)` for site day `index`: the receiving crew's absolute epoch and
        the whistle as a REMAINDER on the dock's batch-local clocks.

        IDEMPOTENT PER INDEX, and that is the contract — one base, asked for twice.  Both
        leaves of one batch stamp their unload rows from the same epoch because there is
        one crew on one dock, so the first call computes and the rest read.  The identical
        rule, for the identical reason, as `PutawayPool.open_batch`.

        `base` is `max(day.start_of(index), recv_clock)`: the receivers start at shift start
        and work what is standing, or they carry on from where yesterday overrun left them.
        Never either leaf's `arm_clock` -- that is a PICK crew's release instant, and basing
        the dock on it would idle the site receivers whenever a pick crew overran its day
        and would misattribute a picking overrun to the dock's cut.  `deadline` is the rest
        of the day CONTAINING that base, so `base + deadline` is the day's end however far
        the carry has run.

        THIS IS NOT WHAT AN UNCOUPLED RUN DOES, and the divergence is deliberate rather
        than a restatement: the shipped runner bases its receive rows at
        `max(arm_clock, recv_clock)` — this leaf's pick-crew release instant. Under one
        crew on one dock that instant is a property of one channel, so the site base moves
        to the shift start. Any run that adopts this is therefore not row-comparable with
        one that did not, which is why the coordinator does not quietly apply it to the
        one-leaf path (`open_batch` has no caller until a coupled run has one).
        """
        if index == self._open:
            return self._base, self._deadline
        if self.day is None:
            raise RuntimeError(
                'a site receiving clock needs a working day to be based on: no `day` was '
                'bound, so there is no shift start to start the crew at and no whistle to '
                'measure the remainder against. Coupling is an era feature and the era '
                'completes the grid')
        if self._owed_records:
            raise RuntimeError(
                f'site day {index} opened while day {self._open} still owes records from '
                f'{sorted(self._owed_records)}; the carry is committed once per site day, '
                f'after every leaf has reported, and a day that opens early would rebase '
                f'the other leaf rows against an epoch it never ran in')
        if not self._leaves:
            raise RuntimeError(
                'no leaf is bound to this coordinator; bind every leaf before the batch '
                'loop, or the day is opened for a crew nobody is scheduling')
        self._open = index
        self._base = max(self.day.start_of(index), self.recv_clock)
        self._deadline = self.day.remaining(self._base)
        self._owed_records = set(self._leaves)
        self._finish = None
        self._share = None
        return self._base, self._deadline

    # ── the site dock, partitioned: one drain, two channels' rows ──────────────────

    def _partition(self) -> dict:
        """Split THIS site day's dock into per-channel shares.  Runs once per day.

        One dock is one drain, so the rows, the flows and the labour all arrive as site
        totals — and ADR-0005 says the pack-denominated half of them decomposes and belongs
        back on the owning channel's own record.  This is where that happens, once, so the
        two leaves cannot each take the whole (`a-right-site-total-hides-two-wrong-shares`
        is that failure, and `receiving_report` checks 1 and 2 would PASS through it because
        both surfaces delegate to the same dock).

        Every row is routed by its SKU through the same `{sku: leaf}` dict step 1 packs by,
        so an unload row, a repack row and the lot that produced them cannot land in
        different channels.

        THE CLOSURE IS ASSERTED, not assumed: the shares are accrued independently of the
        dock's own counters (the leaves' `receiving_seconds` especially, which is the only
        surface that sees a repack), so summing them back to the dock's totals is a real
        check that the decomposition is complete rather than a restatement.
        """
        depth, unloaded, cut, seconds = self.dock.snapshot()
        shares = {ch: {'depth': 0, 'unloaded': int(self._unloaded.get(ch, 0)),
                       'cut': int(self._cut.get(ch, 0)), 'seconds': 0.0,
                       'records': [], 'repacks': []}
                  for ch in self._leaves}
        # The dock FLOOR, by regime.  Empty on every standing-yard run — merchandise waits
        # on a trailer, not on the floor — so this walk is free there; it is written out
        # anyway because `depth` is a LEVEL and a level nobody decomposed would be the site's
        # backlog reported twice, once under each channel's name.
        for item in self.dock.items:
            shares[self._channel_of(self._leaf_for(regime_of(item.unit)))]['depth'] += 1
        for key, rows in (('records', self.dock.take_records()),
                          ('repacks', self.dock.drain_repacks())):
            for row in rows:
                shares[self._channel_of(self._owner_of(row[2]))][key].append(row)
        for ch, leaf in self._leaves.items():
            now = float(leaf.receiving_seconds)
            shares[ch]['seconds'] = now - self._recv_seen.get(ch, 0.0)
            self._recv_seen[ch] = now
        self._unloaded, self._cut = {}, {}
        for name, total in (('depth', depth), ('unloaded', unloaded), ('cut', cut)):
            got = sum(s[name] for s in shares.values())
            if got != total:
                raise RuntimeError(
                    f'the site dock reported {name}={total} for site day {self._open} and '
                    f'its channels account for {got} ({ {c: s[name] for c, s in shares.items()} }); '
                    f'a decomposition that does not close means one channel is carrying '
                    f'part of the other receiving, which is the site total wearing one '
                    f'channel name')
        got_s = sum(s['seconds'] for s in shares.values())
        if abs(got_s - seconds) > _SECONDS_TOL:
            raise RuntimeError(
                f'the site dock charged {seconds!r} s in site day {self._open} and its '
                f'leaves accrued {got_s!r} s; the per-leaf seconds come from each leaf own '
                f'`receiving_seconds` and the site total from the dock, so a gap is labour '
                f'one of the two never saw — an unload handed to a leaf that did not book '
                f'it, or a repack charged to a dock no leaf owns')
        # THE SITE'S OWN ROW for this day, parked for `drain_site_totals`.  Recorded AFTER
        # the closure above rather than before it, so a row that reaches the site DB is one
        # the decomposition agreed with -- a site total nobody could account for is a crash,
        # not a row.  Stamped with the coordinator's OWN day index: the driver asserts it
        # against the batch it is collecting, which is how "one batch is one site day" stops
        # being an assumption held in two places.
        self.site_totals.append((self._open, depth, unloaded, cut, seconds))
        self._share = shares
        return shares

    def _take(self, leaf, what: str):
        """`leaf`'s share of `what` for this site day, served exactly once.

        The same "compute once, serve each caller once" contract `open_batch` keeps, and
        for a sharper reason: these are DRAINS.  A leaf that asked twice would take the
        other channel's rows the second time only if the first call had left them —
        popping is what makes the second call raise instead.
        """
        if self._open is None:
            raise RuntimeError(
                f'the site dock was asked for {what!r} before the site day was opened; '
                f'`open_batch` is what decides which day these rows belong to')
        shares = self._share if self._share is not None else self._partition()
        ch = self._channel_of(leaf)
        share = shares.get(ch)
        if share is None or what not in share:
            raise RuntimeError(
                f'the {ch} leaf asked the site dock for {what!r} twice in site day '
                f'{self._open}; one dock is one drain and the second call would hand it '
                f'rows that are no longer anybody')
        return share.pop(what)

    def snapshot_for(self, leaf) -> tuple:
        """`(depth, unloaded, cut, seconds)` — THIS leaf's share of the site dock.

        The site-scoped replacement for `Inventory_Manager.receiving_snapshot`, which
        refuses on a coupled leaf because all four counters are the site's and three of them
        RESET: the first caller would take the site's whole batch and leave the other
        channel reporting an idle dock.  Here the reset happens once, in `_partition`, and
        each leaf is served its own share.
        """
        return tuple(self._take(leaf, k)
                     for k in ('depth', 'unloaded', 'cut', 'seconds'))

    def drain_records_for(self, leaf) -> list:
        """THIS leaf's unload rows for the site day — the replacement for
        `drain_receiving_records`.  Does NOT restart the crew's clocks: that reset has one
        owner and it is `note_records`."""
        return self._take(leaf, 'records')

    def drain_repacks_for(self, leaf) -> list:
        """THIS leaf's repack rows for the site day — the replacement for
        `drain_repack_records`.  Routed by sku like every other row, so a rescue is
        recorded in the warehouse whose merchandise was rescued."""
        return self._take(leaf, 'repacks')

    # ── the site's census, per leaf ───────────────────────────────────────────────

    def dock_depth_for(self, leaf) -> int:
        """Storage units of THIS leaf's merchandise standing on the site dock floor.

        The site-scoped replacement for `Inventory_Manager.dock_depth`.  A LEVEL, so it
        neither drains nor resets and may be read at any instant -- unlike `snapshot_for`,
        which is a per-site-day drain and can be taken once.

        Zero on every standing-yard run by construction: merchandise waits on a trailer
        until a receiver pulls it, and `accept` hands it straight to the put queue.  Written
        out anyway, because "the floor is empty" is a fact about the standing model and not
        a licence for the caller to assume it.
        """
        self._channel_of(leaf)          # refuses an unbound leaf, as every accessor here does
        return sum(1 for item in self.dock.items
                   if self._leaf_for(regime_of(item.unit)) is leaf)

    def transit_census_for(self, leaf) -> tuple:
        """`(rows, qty, depth)` — THIS leaf's share of the site's in-flight merchandise.

        The site-scoped replacement for the three transit reads a leaf refuses:
        `transit_snapshot` (the replay rows), `in_transit_qty` (the units level) and the
        `lead_queue_depth` a leaf would take from `transit.depth`.  All three come off ONE
        census pass so they cannot disagree with each other, which is the failure two
        separate walks of a live yard invite.

        THE SPLIT IS BY SKU, which is the pack rule ADR-0005 settles: a lot has exactly one
        owning channel however mixed the trailer carrying it.  `rows` and `qty` therefore
        SUM across the leaves to the site's own census exactly.

        `depth` is the one that needs saying out loud.  Uncoupled it counts in-flight
        ENTRIES, which under a trailer pipeline are TRAILERS — and a trailer is mixed by
        construction, so it decomposes to nothing.  Here it counts this leaf's in-flight
        LOTS instead, which is what `BatchTransit.depth` counted before the trailer pipeline
        existed and is the only reading that both decomposes and sums.  A mixed trailer
        therefore contributes one lot to each channel rather than one trailer to both: the
        "two copies of one yard sum to twice the trailers" outcome ADR-0005 rejected is what
        counting trailers here would produce.
        """
        self._channel_of(leaf)
        rows = [r for r in self.transit.snapshot() if self._owner_of(r[0]) is leaf]
        return rows, sum(int(r[1]) for r in rows), len(rows)

    # ── the site's trailer stamps ─────────────────────────────────────────────────

    def drain_trailer_stamps(self) -> list:
        """The site's FINISHED trailer stamps for this batch, and start the list over.

        Trailer-denominated, so the site's and not a channel's (ADR-0005).  Both leaves
        hold this one transit, so a leaf draining it would take every trailer on the site
        into its own table and leave the other channel's yard looking empty — which is why
        `Inventory_Manager.drain_yard_trailers` refuses once the scope is the site's.
        """
        drain = getattr(self.transit, 'drain_stamps', None)
        return drain() if drain is not None else []

    def standing_trailer_stamps(self) -> list:
        """Stamps for trailers STILL ON SITE at run end — read, never drained.

        Worse than the drain above if a leaf were left to do it: this one does NOT empty
        anything, so BOTH leaves would report the same censored trailers and every detention
        day at run end would be counted twice.
        """
        standing = getattr(self.transit, 'standing_stamps', None)
        return standing() if standing is not None else []

    def note_records(self, leaf, finish) -> None:
        """`leaf` has stamped its unload rows; `finish` is the absolute instant its last
        one ended, or None when it recorded nothing.

        Called once per leaf per site day, on BOTH the picked and the skipped path — a
        batch that picked nothing still drained the dock.  When the last leaf reports, the
        site carry is committed.  THE RESET HAS ONE OWNER for exactly the reason the put
        pool says so: one leaf restarting a shared crew's clocks before the other has
        recorded is a silent, plausible-looking zeroing of a half-spent day.

        The carry moves only when somebody actually worked: no records anywhere means the
        crew is where it was, exactly as an unpooled leaf leaves `recv_clock` alone.
        """
        ch = self._channel_of(leaf)
        if self._open is None:
            raise RuntimeError(
                f'the {ch} leaf reported receiving records before the site day was opened; '
                f'`open_batch` is what sets the epoch those rows are stamped from and what '
                f'records who still owes a report')
        if ch not in self._owed_records:
            raise RuntimeError(
                f'the {ch} leaf reported receiving records twice in site day {self._open}; '
                f'the second report would commit a carry over a day the other leaf has '
                f'already been rebased out of')
        self._owed_records.discard(ch)
        if finish is not None:
            self._finish = finish if self._finish is None else max(self._finish, finish)
        if self._owed_records:
            return
        # THE RESET, and this method is its ONE owner on a coupled run.  Uncoupled it
        # belongs to `Dock.drain_records`, which a leaf reaches through
        # `drain_receiving_records` -- and that accessor REFUSES on a coupled leaf (the
        # first caller would take the other channel's rows and restart the crew's clocks
        # half-way through the site's batch).  Removing the old owner without appointing a
        # new one is the silent version of this whole file: the dock's clocks would
        # accumulate across the arm while the runner kept adding an epoch, so every row
        # after day 0 would be stamped late by every preceding day's receiving seconds, and
        # eventually `can_start` would be false from the first unload and the site dock
        # would stop receiving while `cut` reported a full backlog.  `Dock.drain_records`'
        # own docstring is where that failure is written down.
        self.dock.reset_clocks()
        if self._finish is not None:
            self.recv_clock = self._finish

    def _channel_of(self, leaf) -> str:
        for ch, lf in self._leaves.items():
            if lf is leaf:
                return ch
        raise ValueError(
            'a leaf reported to this coordinator without being bound to it; the site day '
            'is opened for the leaves the coordinator knows about, so an unbound one would '
            'commit a carry nobody accounted for')

    def drain_site_rows(self) -> list:
        """This drain window's SITE-scoped yard rows, and start the list over.

        A coupled drain's `(yard_start, free_doors_start, yard_end, remainder)` belongs to
        neither leaf -- it is a statement about the site's trailers and the site's doors --
        so `receive` parks it here instead of handing an arbitrary leaf a number that reads
        as its channel's.  Nothing consumes this yet; `<pair>/_site/inbound_<pair>.db` is
        the declared home and its contract bump is the next ticket's.
        """
        out, self.site_rows = self.site_rows, []
        return out

    def drain_site_totals(self) -> list:
        """This drain window's SITE dock totals, `[(day, depth, unloaded, cut, seconds)]`,
        and start the list over.

        The twin of `drain_site_rows`, and drained on the same cadence for the same reason:
        a list that only grows is the only evidence the site day was partitioned at all, and
        compounding it would hand the writer several days' counters stamped as one.

        NORMALLY ONE ROW per call -- `_partition` runs once per site day -- but the list is
        returned whole rather than popped singly, because a caller that took "the" row would
        silently drop the second one if a grid ever put two site days in one batch.  The
        driver asserts the day index instead, which fails loudly on exactly that.
        """
        out, self.site_totals = self.site_totals, []
        return out

    # ── the space view ─────────────────────────────────────────────────────────────────

    def _freeze_views(self, leaves, epoch: float) -> list:
        """`[(regime, view)]` — one frozen space view per leaf that runs a timeline.

        ONE LEAF IS CONTRIBUTED UNTAGGED and more than one is TAGGED, and the asymmetry is
        the design rather than a shortcut.  A composition of one partitions nothing, so a
        tag there would decide nothing — and `compose_site_view` returns a lone
        contribution BY IDENTITY, which is what keeps every standing-yard run already on
        disk byte-identical (filtering one leaf's `empties` would remove a phantom that
        belongs to the UNCOUPLED model).  With two, the tag is what partitions `empties` so
        a mixed trailer's store units rank against store bins, and the composer REFUSES an
        untagged contribution the moment there are two ("Design the site space view").

        The tag is the channel the leaf BOUND under, which is the same knowledge the
        `{sku: leaf}` owner dict is built from — not `regime_of` on anything, because the
        coordinator is the one object that holds both managers and knows which is which.
        """
        leaves = tuple(leaves)
        tagged = len(leaves) > 1
        out: list = []
        for leaf in leaves:
            if leaf.space_timeline is None:
                continue
            tag = self._channel_of(leaf) if tagged else None
            out.append((tag, leaf.space_timeline.freeze(leaf, epoch)))
        return out

    # ── the drain ─────────────────────────────────────────────────────────────────

    def receive(self, leaves, deadline: float | None) -> tuple | None:
        """One drain of the standing dock: plan arrivals, fill doors, unload, hand off.

        `leaves` is a SEQUENCE, and there is one drain for the whole site however many it
        holds.  A coordinator serving AT MOST ONE leaf routes and returns exactly as the
        single-channel drain always did: the yard row comes back and the caller decides
        where it belongs (`_receive` appends it to that leaf's `_yard_drains`).  From the
        SECOND leaf the row is site-scoped and belongs in neither leaf's table, so it is
        parked on `site_rows` and None comes back — a leaf cannot record a number that is
        not its channel's if it is never handed one ("Design the site scope in the run
        tree").  That threshold is the one `bind` stamps `site_scoped` on and
        `_freeze_views` tags on, deliberately: three spellings of "is the site real yet"
        is three things to keep in step.

        TWO REFUSALS, and they are the same rule from both sides.  A coordinator serving
        several leaves refuses a drain that does not name all of them — the only way to
        reach that is a coupled leaf's own `check_reorders`, and what it would do is drain
        the site's dock for one channel while the other's arrivals are still on the yard:
        half a site day's receiving, attributed whole.  An UNBOUND coordinator refuses more
        than one leaf outright, because the owner dict is built by `bind` and without it
        there is no way to say whose merchandise a lot is.

        Four steps, and their order is the design:

        1. PLANS-AT-ARRIVAL.  Every trailer that joined the yard gets its pack plan NOW,
           per contiguous lot — the same portions the v1 drain packs — and its units are
           stamped immediately, so a pallet that stands three batches in the yard is three
           batches old when it finally reaches floor space.  The merchandise stays in
           `_deferred_qty`: nothing is queued until a crew actually pulls it.
        2. THE DOOR FILL, NOT budget-gated.  Staging is yard-jockey work, not receiving
           labour, so even a zero-budget drain fills every free door from the drain-frozen
           yard ranking.
        3. THE UNLOAD, budget-gated, per the crew-allocation mode ('split' door teams or
           the 'merged' pooled gang).  The whistle is a START gate, one unload of overtime
           per worker, exactly as the v1 path's.  Same-drain refills consume the frozen
           rankings; no mid-drain re-scoring.
        4. THE CANONICAL HANDOFF.  Whatever the allocation, unloaded units reach `_queue`
           in merged order — trailers by dock rank, units by local rank, filtered to what
           actually unloaded — never in labor-completion order.  That is the containment
           property: crew allocation moves labor stamps and makespans ONLY, never
           placement physics.  The deferred->queued flip rides the handoff, per unit, so
           `position = on_hand + queued + deferred` never wobbles.
        """
        dock, transit = self.dock, self.transit
        leaves = tuple(leaves)
        if not leaves:
            raise ValueError('a drain with no leaf has nobody to pack for and nobody to '
                             'hand merchandise to')
        # ONE DOCK IS ONE DRAIN.  A BOUND coordinator is drained for every leaf it serves or
        # for none: a partial drain would unload the site day for one channel while the
        # other's arrivals stood on the yard.  An UNBOUND one has no owner dict, so it
        # cannot route a second leaf at all -- both halves are the same rule, and they key
        # on the same fact so neither can drift past the other.
        if self._leaves:
            if set(map(id, leaves)) != set(map(id, self.leaves)):
                raise RuntimeError(
                    f'a drain of the site dock named {len(leaves)} leaf/leaves while the '
                    f'coordinator serves {tuple(self._leaves)!r}; one dock is one drain, '
                    f'and a partial one would unload the site day for one channel while '
                    f'the other arrivals stand on the yard')
        elif len(leaves) > 1:
            raise RuntimeError(
                f'{len(leaves)} leaves were handed to a coordinator none of them is bound '
                f'to; the `{{sku: leaf}}` owner dict is built by `bind`, so an unbound '
                f'coordinator has no way to say whose merchandise a lot is')
        # THE WHISTLE MUST BE THE DAY THE CLOCKS ARE RUNNING ON, checked rather than
        # trusted -- `PutawayPool.drain` states the reason and it is the same one: a crew
        # gated on a different day than the epoch its rows are stamped from does work
        # nobody has the hours for, and every row of it looks ordinary.
        if self._open is not None and deadline != self._deadline:
            raise RuntimeError(
                f'the site dock drained against a deadline of {deadline!r} but site day '
                f'{self._open} has {self._deadline!r} left; one crew on one dock works one '
                f'day, and the base its rows are stamped from is that day start')
        # ONE DRAIN IS ONE INSTANT.  The yard's calendar is the SITE's, so a drain stamped
        # with two epochs would be two arrival calendars over one set of doors.  Checked
        # rather than picked from the first leaf: the two leaves' pick crews genuinely
        # release at different instants inside one site day, and it is the DRIVER's job to
        # hand the site epoch down -- silently taking leaf[0]'s would make that a detail
        # nobody could see was wrong.
        stamps = [lf._now_s for lf in leaves]
        if any(t is None for t in stamps) and any(t is not None for t in stamps):
            raise ValueError(
                f'some leaves of one site drain carry an epoch and some carry none '
                f'({stamps!r}); an unstamped leaf is not "the same instant as the others", '
                f'it is a leaf the driver forgot to hand the site epoch to')
        _known = [t for t in stamps if t is not None]
        if _known and max(_known) - min(_known) > _EPOCH_TOL:
            raise ValueError(
                f'the leaves of one site drain carry different epochs {sorted(_known)!r}; '
                f'one dock drains at one instant, and two would rank the same yard against '
                f'two different "now"s')
        epoch = _known[0] if _known else 0.0
        source = getattr(transit, 'SOURCE', 'reorder')
        ctx = transit.freeze_ctx()
        # CTX-FREEZE IS VIEW-FREEZE: one space projection per drain serves every decision
        # in it (no per-decision rescans).  `ctx.space` is the named-view arrival point
        # the priority seams reserved; every seeded 'fifo' key ignores it, so with both
        # policies 'fifo' the view is pure data -- neutrality rides the degenerate
        # lockstep (test_space_timeline).
        #
        # THE FREEZE STAYS THE LEAF'S AND THE COMPOSITION IS THE SITE'S.  `freeze` keeps
        # its single-manager signature and its purity pin; `compose_site_view` is a pure
        # function over already-frozen data, and it is reached through here TODAY with one
        # contribution -- which it returns BY IDENTITY, so this line is what it always was.
        # With two leaves it becomes two contributions, each tagged with its channel, and
        # the tag is what partitions `empties` so a mixed trailer's store units rank
        # against store bins ("Design the site space view").
        views = self._freeze_views(leaves, epoch)
        if views:
            ctx.space = compose_site_view(views)

        # THE OWNER ROUTE FOR STEP 1, and ONE threshold decides it.  The site is "real"
        # from the SECOND leaf -- the same fact `bind` stamps `site_scoped` on and
        # `_freeze_views` tags its contributions on -- so a coordinator serving at most one
        # leaf keeps the historical behaviour: the single leaf the drain was handed packs
        # everything and takes everything.  Keyed on the LEAVES SERVED and not on whether
        # the owner dict happens to be populated, because "I bound one leaf to get the site
        # clock" does not mean "route me as a site", and three spellings of the same
        # threshold in one file is three things to keep in step.
        _solo = leaves[0] if len(self._leaves) <= 1 else None

        # 1. plans-at-arrival (leaf-side: the transit can reach neither _originals nor
        #    the packer).  Stamped in yard order, so ages are monotone with arrival.
        #    Each lot is packed by ITS OWNER -- `_originals`, `inbound_split`, the packer
        #    and `_putaway_seq` are all one channel's, so a mixed trailer is planned lot by
        #    lot across two leaves.  Stamp ordering needs nothing extra: `_stamp` increments
        #    a PER-LEAF sequence and each leaf's put queue only ever compares its own, so
        #    per-leaf FIFO survives a mixed trailer without any cross-leaf sequencing.
        plans_new: list = []
        for trailer in transit.unplanned():
            items: list = []
            tplans: list = []
            for sku, qty in transit.planned_lots(trailer, ctx):
                owner = _solo if _solo is not None else self._owner_of(sku)
                lot_plans, lot_items = owner.plan_lot(sku, qty, source)
                tplans.extend(lot_plans)
                items.extend(lot_items)
            trailer.plans = tplans
            trailer.pending = items
            trailer.taken = 0
            plans_new.extend(tplans)
            if not items:
                # Nothing packed at all: no work to hold a door open for.
                transit.discard(trailer, epoch)
        dock.note_arrivals(plans_new)

        # 2. the door fill — NOT budget-gated (yard-jockey work, not crew labour).
        yard_next = deque(transit.yard_order(ctx))
        while transit.free_doors > 0 and yard_next:
            transit.stage(yard_next.popleft(), epoch)
        # The drain-frozen DOCK ranking, over everything now staged (carried remainders
        # and fresh stagings alike): the allocation preference and the handoff order.
        work_order = transit.dock_order(ctx)

        # 3. the unload, per the allocation mode.  The DOOR-TEAM CAP is trailer physics
        #    (at most `cap` receivers can support one trailer's unload and pack at once),
        #    so it is read here once and applies in BOTH modes; None is today's uncapped
        #    dealing, byte-identically.  Only the standing transit carries it -- the v1
        #    path never reaches this method and never reads the knob.
        cap = getattr(transit, 'door_team', None)
        if getattr(transit, 'allocation', 'merged') == 'split':
            done = self._unload_split(dock, transit, deadline, epoch,
                                      work_order, yard_next, cap)
        else:
            done = self._unload_merged(dock, transit, deadline, epoch,
                                       work_order, yard_next, cap)

        # 4. the canonical handoff, with the per-unit ledger flip.  `dock.seconds`
        #    accrues HERE, in canonical order, for both allocation modes: summed in
        #    charge order instead, split's float association differs from merged's by
        #    an ulp, and "identical except labor stamps" stops being byte-true.
        #
        #    The leaf's half and the dock's half are separate statements over DISJOINT
        #    state, so the split costs no byte: each accumulator still sees the same
        #    `dur` values in the same order it did when both halves were one loop body.
        #
        #    THE OWNER ROUTE FOR STEP 4 IS `regime_of`, not the owner dict: a unit exists
        #    here, and `regime_of` is single-valued per entity and already answers which
        #    warehouse it belongs in (CLAUDE.md's reuse list).  Nothing is stamped, carried
        #    or mapped at the unit level to make that true.  The two routes are then
        #    CROSS-CHECKED: the dict said who packs the lot, the regime says who takes the
        #    unit, and a disagreement is the one shape in which the catalogue partition and
        #    the regime tagging can differ -- a ledger that balances in the wrong warehouse.
        # The cross-check is a per-SKU fact, so it is memoised per SKU rather than re-run
        # per unit: a trailer carries many units of few SKUs, and `regime_of` walks up to
        # six `getattr`s.  The GUARANTEE is unchanged -- every unit still routes through a
        # taker that was checked against the owner dict.
        _takers: dict = {}
        for trailer, recs in done:
            for item, t0, dur, w in recs:
                unit = item.unit
                if _solo is not None:
                    taker, _ch = _solo, None
                else:
                    sku = unit.order.sku
                    taker, _ch = _takers.get(sku, (None, None))
                    if taker is None:
                        taker = self._leaf_for(regime_of(unit))
                        if self._owner_of(sku) is not taker:
                            raise ValueError(
                                f'sku {sku} was packed by the leaf the owner dict names '
                                f'and unloads as a {regime_of(unit)!r} unit, which names '
                                f'another; the catalogue partition and the regime tagging '
                                f'disagree, so one of the two ledgers this unit touches is '
                                f'the wrong warehouse')
                        _ch = self._channel_of(taker)
                        _takers[sku] = (taker, _ch)
                taker.accept(item, dur)
                dock.records.append((t0, dur, unit.order.sku, unit.quantity, w))
                dock.unloaded += 1
                dock.seconds += dur
                # THE DECOMPOSABLE HALF, accrued where the owner is already resolved.  The
                # dock counts one site total; ADR-0005 puts the pack-denominated count back
                # on the owning channel, and `_partition` closes the two against each other.
                if _ch is not None:
                    self._unloaded[_ch] = self._unloaded.get(_ch, 0) + 1

        # What the whistle cost: the remainders standing on STAGED trailers, in storage
        # units, counted once.  The yard is never cut — waiting there is calendar, the
        # fee proxy's domain, not a labour boundary's.
        left = 0
        by_owner: dict = {}
        for t in transit.staged():
            if t.pending is None:
                continue
            left += len(t.pending) - t.taken
            if _solo is None:
                # A remainder is merchandise, so it has an owner exactly as an unloaded
                # unit does.  Counted here rather than derived later: `pending` is consumed
                # by the next drain, so this is the last instant the split is knowable.
                for it in t.pending[t.taken:]:
                    _c = self._channel_of(self._leaf_for(regime_of(it.unit)))
                    by_owner[_c] = by_owner.get(_c, 0) + 1
        if deadline is not None and left:
            dock.cut += left
            for _c, _n in by_owner.items():
                self._cut[_c] = self._cut.get(_c, 0) + _n

        # THE DRAIN'S ROW.  Two pairs, and they answer two different questions.  The START
        # pair is CONTENTION — standing trailers against free doors at freeze, which is
        # what "did the yard bind" means before anything was served.  The END pair is the
        # BINDING CUT — trailers this drain never reached and units it left on a door.
        # Both are LEVELS: they are re-measured every drain and summing either across
        # drains restates the same standing trailers once per batch (the `recv_cut` scar).
        # `left` is computed above the whistle test, not inside it: a drain that ran out of
        # WORK leaves the same remainder as one that ran out of DAY, and only one of those
        # is a cut — the level says what was standing either way.
        row = (ctx.yard_depth, ctx.free_doors, transit.yard_depth, left)
        if _solo is not None:
            return row
        # SITE-SCOPED, so no leaf is handed it.  Same threshold as the routing above, for
        # the same reason: a row parked here while the caller still expected one back is a
        # yard row nothing ever writes.  See `drain_site_rows`.
        self.site_rows.append(row)
        return None

    # ── the phase composition ─────────────────────────────────────────────────────

    def drain(self, leaves, put_deadline: float | None = None,
              recv_deadline: float | None = None, now_s: float | None = None) -> dict:
        """Drive N leaves' phases with ONE shared receive, and return the SKUs each
        triggered, keyed by `id(leaf)`.

        Keyed by identity rather than by the leaf itself because a manager is unhashable
        by value here and the caller already holds the leaves it passed in; a caller doing
        `triggered[leaf]` gets a `KeyError`, so the key is spelled out.

        `SITE_PHASES` IS THIS BODY, and the module constant is where the two facts it
        encodes are argued: which phases are the SITE's, and why the loop is phase-major.
        The composition is the same seven phases `check_reorders` composes for one channel,
        in the same order, because THE ORDER IS THE BEHAVIOUR — firing before the lead tick
        would decrement an order in the batch it was placed, and releasing before firing
        would delay every lead-0 arrival by a batch.

        Phase 3 (`_release_arrivals`) runs per leaf even though it is a structural no-op
        in standing mode — it is the phase that lands trailers in the yard, so it must
        run for every leaf BEFORE the shared receive.  Dropping it because its return is
        empty would strand every arrival.

        Phase 5 is routed exactly as `check_reorders` routes it: to the SITE PUT POOL when
        one is bound, which divides the day between the channels and re-drains both against
        the whole of it, and to the leaf otherwise.  Reached from HERE rather than from each
        leaf's own composition, it lands after the shared receive for every leaf — so a unit
        unloaded this morning gets a bin today, in both channels, which is the whole reason
        the receive sits where it does.

        With a single leaf this is `check_reorders` phase for phase, and
        `Tests/unit/test_site_receiving.py` pins that equality.
        """
        leaves = list(leaves)
        if not leaves:
            raise ValueError('a site drain with no leaf has no phases to run; `receive` '
                             'says the same thing one level down, and this is where the '
                             'first `leaves[0]` would otherwise raise an IndexError')
        triggered: dict = {}
        # PHASE-MAJOR from here down: every leaf runs a phase before any leaf runs the next.
        # See `SITE_PHASES` for why (leaf-major loading makes every trailer channel-pure).
        for leaf in leaves:
            leaf._now_s = now_s
            leaf._tick_batch()
        for leaf in leaves:
            leaf.reclaim_emptied_bins()
        # SITE PHASE.  `_advance_lead_queue` delegates to the ONE transit, which `bind`
        # asserts every leaf holds, so driving it on the first leaf IS driving it on the
        # site; driving it on each would tick every supplier lead once per channel.
        leaves[0]._advance_lead_queue()
        for leaf in leaves:
            triggered[id(leaf)] = leaf._fire_reorders()
        for leaf in leaves:
            leaf._release_arrivals()
        # SITE PHASE: ONE receive, for every leaf at once.  With one leaf the row comes back
        # and goes where it always went; with two it is site-scoped and `receive` parks it.
        row = self.receive(leaves, recv_deadline)
        if row is not None:
            leaves[0]._yard_drains.append(row)
        for leaf in leaves:
            if leaf.putaway_pool is None:
                leaf.drain_putaway(put_deadline)
            else:
                leaf.putaway_pool.drain(leaf, put_deadline)
        return triggered

    # ── the unload modes: dock physics, and no leaf is reachable from either ───────

    def _unload_merged(self, dock, transit, deadline, epoch, work_order, yard_next,
                       cap: int | None = None):
        """The 'merged' pooled gang: v1's physics kept as the verification bridge.

        One crew works trailers strictly in dock-rank order, completing the top-ranked
        first; a freed door pulls the frozen-ranking-next trailer, which joins the END of
        the work list.  In the degenerate configuration (FIFO, doors >= every trailer, no
        cap) the charge sequence is exactly the v1 drain's — the lockstep pin.
        Under a door-team `cap` the gang IS one team of `cap` on one trailer — the cap is
        a property of the trailer, not of the dealing rule — so the rest of the crew
        idles; None keeps the whole crew, byte-identically.
        Returns [(trailer, [(item, t0, dur, worker), ...])] in canonical drain order.
        """
        done: list = [(t, []) for t in work_order]
        # A team of everybody IS the pooled gang; charge_team so `seconds` accrues at
        # the handoff (canonical order) rather than here — see that loop's comment.
        gang = list(range(dock.crew_size))
        if cap is not None:
            gang = gang[:cap]
        idx = 0
        while idx < len(done):
            trailer, recs = done[idx]
            pend = trailer.pending or []
            gated = False
            while trailer.taken < len(pend):
                if not dock.can_start(deadline):
                    gated = True
                    break
                item = pend[trailer.taken]
                order = item.unit.order
                # `unit=` is read only by a SITE dock, whose price list is keyed by the
                # merchandise's own regime (site-dock 27); an uncoupled dock ignores it.
                dur = dock.unload_seconds(order.weight, order.volume(),
                                          item.unit.quantity, unit=item.unit)
                t0, w = dock.charge_team(gang, dur)
                recs.append((item, t0, dur, w))
                trailer.taken += 1
            if gated:
                break
            at = epoch + (recs[-1][1] + recs[-1][2] if recs else 0.0)
            transit.door_freed(trailer, at)
            if yard_next:
                nxt = yard_next.popleft()
                transit.stage(nxt, at)
                done.append((nxt, []))
            idx += 1
        return done

    def _unload_split(self, dock, transit, deadline, epoch, work_order, yard_next,
                      cap: int | None = None):
        """The 'split' door teams: the standing model's own physics.

        At ctx-freeze the workers are DEALT across staged trailers in dock-priority
        order, cycling, so the top ranks take the extras when the division is uneven
        (`allocation.partition`, round-robin).  Each team charges earliest-free WITHIN
        the team.  Doors therefore free STAGGERED — the realistic dynamic the fee and
        space signals need — and when one does, the yard-pull stages the frozen-next
        trailer and the freed team reassigns: (1) to the top-ranked staged trailer with
        NO workers — the one-worker-many-doors inversion's guard — (2) else to its own
        door's replacement, (3) else to the top-ranked trailer with the fewest workers.
        A worker idles only when nothing staged has units.  Dock priority is thereby a
        worker-ALLOCATION preference: decisive when workers < staged trailers, graded
        otherwise.

        THE DOOR-TEAM CAP (`cap`; None = uncapped, the dealing above verbatim).  At most
        `cap` receivers support one trailer's unload and pack at once, every worker
        additive, the steps inside an unload not modelled.  The deal stays EVEN and each
        team is then cut to `cap`; the cut-off workers simply are not dealt, and idle for
        the drain.  Even, not greedy: 22 receivers over three staged trailers deal 8/7/7,
        and the cap binds only when the even division would put more than `cap` on a door
        (22 over two doors is 10/10, with two standing idle until a door frees).  A greedy
        deal — 10/10/2 — is the rule 25 rejected: it makes the cap bind at every door
        count and turns dock priority into a capacity grant.

        The reassignment respects the cap by SPREADING a freed team over the (1)-(2)-(3)
        targets in order, each taking up to its own room, instead of handing the whole team
        to one.  That matters at step (3), where the target already has a team: the
        pre-cap code extended it unconditionally, which under a cap would put `2 x cap`
        receivers on one trailer.  Uncapped the room is unbounded, so the head of the list
        takes the whole team and the None path is byte-identical rather than merely
        equivalent.  Nothing is dealt to a cut worker later: whenever a target has room it
        is a FRESH staging with room `cap`, and a freed team that was cut is itself exactly
        `cap`, so it fills the door alone.

        The loop advances whichever team can start soonest, so charges interleave in
        true clock order and a reassignment always sees every earlier emptying's effect.
        Returns the same shape as `_unload_merged`, in the same canonical order.
        """
        done: list = [(t, []) for t in work_order]
        recs_of = {id(t): recs for t, recs in done}
        alive: list = list(work_order)
        teams: dict = {}
        if alive:
            crew = list(range(dock.crew_size))
            for trailer, team in zip(alive, partition(crew, len(alive))):
                teams[id(trailer)] = team if cap is None else team[:cap]
        while True:
            best = None
            best_ns = 0.0
            for trailer in alive:
                team = teams.get(id(trailer))
                if not team or trailer.taken >= len(trailer.pending or []):
                    continue
                ns = dock.team_next_free(team)
                if best is None or ns < best_ns:
                    best, best_ns = trailer, ns
            if best is None:
                break                              # nothing workable anywhere
            if deadline is not None and best_ns >= deadline:
                break                              # the START gate, globally: best_ns is
                                                   # the min over teams, so nobody can
            item = best.pending[best.taken]
            order = item.unit.order
            dur = dock.unload_seconds(order.weight, order.volume(),
                                       item.unit.quantity, unit=item.unit)
            t0, w = dock.charge_team(teams[id(best)], dur)
            recs_of[id(best)].append((item, t0, dur, w))
            best.taken += 1
            if best.taken < len(best.pending):
                continue
            # The trailer came up empty: the door frees AT THAT INSTANT (staggered, not
            # at the drain boundary), the yard-pull fires, and the freed team reassigns.
            at = epoch + t0 + dur
            transit.door_freed(best, at)
            freed = teams.pop(id(best))
            alive.remove(best)
            nxt = None
            if yard_next:
                nxt = yard_next.popleft()
                transit.stage(nxt, at)
                teams[id(nxt)] = []
                alive.append(nxt)
                recs: list = []
                done.append((nxt, recs))
                recs_of[id(nxt)] = recs
            live = [t for t in alive
                    if t.pending is not None and t.taken < len(t.pending)]
            # The reassignment order, (1)-(2)-(3) as one ranked list rather than one
            # target: uncapped, the head of the list takes the whole team (the single
            # target the three steps used to resolve to); under the cap each takes up to
            # its room and the tail spills to the next.
            pos = {id(t): i for i, t in enumerate(alive)}
            targets = [t for t in live if not teams.get(id(t))]                 # (1)
            if nxt is not None and nxt in live and nxt not in targets:          # (2)
                targets.append(nxt)
            targets.extend(sorted((t for t in live if t not in targets),        # (3)
                                  key=lambda t: (len(teams.get(id(t), ())),
                                                 pos[id(t)])))
            for target in targets:
                if not freed:
                    break
                room = (len(freed) if cap is None
                        else max(0, cap - len(teams.get(id(target), ()))))
                if room <= 0:
                    continue
                teams[id(target)].extend(freed[:room])
                freed = freed[room:]
            # Anything still in `freed` idles: every staged trailer with work is at its cap.
        return done
