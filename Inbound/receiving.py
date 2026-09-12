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
leaves and reaches each one through exactly two public ports:

    leaf.plan_lot(sku, qty, source) -> (plans, items)
        Step 1's per-lot body: resolve `_originals`, apply `inbound_split`, pack, stamp,
        and debit the pack shortfall against the remainder ledger.
    leaf.accept(item, dur) -> None
        Step 4's per-unit body: the deferred->queued flip, `_recv_seconds`, `_queue`.

Everything else a drain touches is the coordinator's own: the `Dock`, the `YardTransit`,
the ctx freeze, the door fill, the unload and the handoff ORDER.  `regime_of` (wh_kernel,
dependency-free) is what will route a unit to its owning leaf once there are two; with
one leaf every unit routes to it trivially, and this module is deliberately written so
that adding the second leaf changes the ROUTING and nothing else.

# ── what is NOT here yet ──────────────────────────────────────────────────────────

The `{sku: leaf}` owner dict, the refusal on a non-standing transit, and the `SITE_PHASES`
interleave for two leaves all wait for a second leaf to exist ("Design the site receiving
coordinator", the site-dock map).  `drain` below is the one-leaf composition and states
its own limit.

The SPACE VIEW is already site-shaped: `_freeze_views` collects one frozen view per leaf
and `Inbound/site_space.compose_site_view` turns them into the one view a drain reads.
With one leaf it composes one and returns it by identity; the second leaf changes what
`_freeze_views` returns and nothing else.
"""
from __future__ import annotations

from collections import deque

from Warehouse.kernel.allocation import partition

from Inbound.site_space import compose_site_view


class SiteReceiving:
    """The site's dock, yard and drain record, driving N leaves through two ports.

    Constructed by the driver where the `Dock` and the `YardTransit` are built, and bound
    onto each leaf it serves (`mgr.receiving`) — the same injection precedent as
    `enable_receiving` and `mgr.packer`: the broker holds what it is handed, and nothing
    under `Warehouse/` imports this package.
    """

    def __init__(self, dock, transit):
        #: The one dock: crew clocks, the unload cost model, and the `records` /
        #: `unloaded` / `seconds` / `cut` counters the run reports from.
        self.dock = dock
        #: The one yard: trailer, yard and door STATE.  This object owns the decisions
        #: that state is consulted for.
        self.transit = transit

    # ── the space view ─────────────────────────────────────────────────────────────────

    def _freeze_views(self, leaf, epoch: float) -> list:
        """`[(regime, view)]` — one frozen space view per leaf that runs a timeline.

        ONE LEAF TODAY, and it is contributed UNTAGGED: a composition of one partitions
        nothing, so there is no decision for a regime tag to make and inventing one here
        would be a value nothing checks.  The tag arrives with the owner routing — the
        coordinator is the thing that holds both managers and calls `freeze` on each,
        which is the same knowledge the `{sku: leaf}` dict is built from ("Design the site
        space view", section 5) — and `compose_site_view` REFUSES an untagged contribution
        the moment there are two, so the absent tag cannot survive into the coupled case.

        A method rather than an inline expression so the second leaf changes THIS and
        nothing in `receive`.
        """
        if leaf.space_timeline is None:
            return []
        return [(None, leaf.space_timeline.freeze(leaf, epoch))]

    # ── the drain ─────────────────────────────────────────────────────────────────

    def receive(self, leaf, deadline: float | None) -> tuple:
        """One drain of the standing dock: plan arrivals, fill doors, unload, hand off.

        Returns the drain's yard row `(yard_start, free_doors_start, yard_end, remainder)`
        rather than recording it, so the caller decides where a row belongs — one leaf's
        `_yard_drains` today, a site-scoped artifact once there are two ("Design the site
        scope in the run tree").

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
        epoch = leaf._now_s if leaf._now_s is not None else 0.0
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
        # against store bins ("Design the site space view").  Wired at one leaf rather
        # than left for the second, because a composer nothing calls is a composer nobody
        # finds out is wrong.
        views = self._freeze_views(leaf, epoch)
        if views:
            ctx.space = compose_site_view(views)

        # 1. plans-at-arrival (leaf-side: the transit can reach neither _originals nor
        #    the packer).  Stamped in yard order, so ages are monotone with arrival.
        plans_new: list = []
        for trailer in transit.unplanned():
            items: list = []
            tplans: list = []
            for sku, qty in transit.planned_lots(trailer, ctx):
                lot_plans, lot_items = leaf.plan_lot(sku, qty, source)
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
        #    Routing to the owning leaf goes HERE when there are two of them.
        for trailer, recs in done:
            for item, t0, dur, w in recs:
                unit = item.unit
                leaf.accept(item, dur)
                dock.records.append((t0, dur, unit.order.sku, unit.quantity, w))
                dock.unloaded += 1
                dock.seconds += dur

        # What the whistle cost: the remainders standing on STAGED trailers, in storage
        # units, counted once.  The yard is never cut — waiting there is calendar, the
        # fee proxy's domain, not a labour boundary's.
        left = sum(len(t.pending) - t.taken for t in transit.staged()
                   if t.pending is not None)
        if deadline is not None and left:
            dock.cut += left

        # THE DRAIN'S ROW.  Two pairs, and they answer two different questions.  The START
        # pair is CONTENTION — standing trailers against free doors at freeze, which is
        # what "did the yard bind" means before anything was served.  The END pair is the
        # BINDING CUT — trailers this drain never reached and units it left on a door.
        # Both are LEVELS: they are re-measured every drain and summing either across
        # drains restates the same standing trailers once per batch (the `recv_cut` scar).
        # `left` is computed above the whistle test, not inside it: a drain that ran out of
        # WORK leaves the same remainder as one that ran out of DAY, and only one of those
        # is a cut — the level says what was standing either way.
        return (ctx.yard_depth, ctx.free_doors, transit.yard_depth, left)

    # ── the phase composition ─────────────────────────────────────────────────────

    def drain(self, leaves, put_deadline: float | None = None,
              recv_deadline: float | None = None, now_s: float | None = None) -> dict:
        """Drive N leaves' phases with ONE shared receive, and return {leaf: triggered}.

        The site composition of the same seven phases `check_reorders` composes for one
        channel.  THE ORDER IS THE BEHAVIOUR, exactly as it is there: phases 0-3 are the
        CALENDAR and run per leaf (a lead time elapses whether or not anyone is at work);
        the receive is ONE shared drain because there is one dock; the put drain is
        labour and runs per leaf.

        Phase 3 (`_release_arrivals`) runs per leaf even though it is a structural no-op
        in standing mode — it is the phase that lands trailers in the yard, so it must
        run for every leaf BEFORE the shared receive.  Dropping it because its return is
        empty would strand every arrival.

        ONE LEAF TODAY.  With a single leaf this is `check_reorders` phase for phase, and
        `Tests/unit/test_site_receiving.py` pins that equality.  The put pool that makes
        phase 5 a shared budget is a separate object ("Design the site put-away pool");
        until it exists, each leaf drains its own put queue against its own deadline.
        """
        leaves = list(leaves)
        triggered: dict = {}
        for leaf in leaves:
            leaf._now_s = now_s
            leaf._tick_batch()
            leaf.reclaim_emptied_bins()
            leaf._advance_lead_queue()
            triggered[id(leaf)] = leaf._fire_reorders()
            leaf._release_arrivals()
        # ONE receive for the site.  The row goes to the leaf that owns the drain record
        # today; with two leaves it becomes a site-scoped artifact instead.
        row = self.receive(leaves[0], recv_deadline)
        leaves[0]._yard_drains.append(row)
        for leaf in leaves:
            leaf.drain_putaway(put_deadline)
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
                dur = dock.unload_seconds(order.weight, order.volume(),
                                          item.unit.quantity)
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
            dur = dock.unload_seconds(order.weight, order.volume(), item.unit.quantity)
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
