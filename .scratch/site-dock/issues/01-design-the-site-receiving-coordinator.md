# Design the site receiving coordinator

Type: grilling
Status: resolved

HITL. Skills: `grilling` + `domain-modeling` + `codebase-design`.

## Question

The standing receiving drain is a ~220-line **manager method** — `_receive_standing`
(`Warehouse/inventory/inventory_reorder.py:706-826`, with `_unload_merged` `:828-866` and
`_unload_split` `:868-944`) — that reaches `transit.unplanned()`, `self.packer`, `self._originals`,
`dock.note_arrivals`, `transit.stage/dock_order/door_freed` and the per-unit ledger flip, all as
one leaf's. Under one site dock it must live **above both managers**. Decide what that thing is,
where it lives, and what each manager keeps.

Four sub-questions, all in scope here:

1. **The object.** A site receiving coordinator holding the one `Dock`, the one transit and the
   one yard, with the two managers injected? Or the drain stays a manager method and one manager
   is elected to run it for both? (`Inventory_Management.py:275` `self._dock`, `:1141`
   `enable_receiving`, `:1763`/`:1804`/`:1850` `self._dock.takes(source)` all read the dock as
   *mine*; `:254` and `:620-648` do the same for transit.) Note the cheap half: these are already
   injection seams, so one object can serve two managers without touching `Warehouse/`.

2. **The phase order.** `check_reorders` (`inventory_reorder.py:956-1004`) is six phases, per
   manager, sequential — "THE ORDER IS THE BEHAVIOUR", pinned by `Tests/unit/test_reorder_phases.py`.
   Coupled, the charter implies phases 1–3 (tick, reclaim, advance) run for **both** leaves, then
   one shared phase 4 (receive), then phase 5 per leaf. Confirm or replace that interleave, and
   decide whether it is a site-level contract with its own test or an extension of the existing one.
   `strategy_runner.py:1358` is the single call site.

3. **Pack routing at the release seam.** The charter settles pack-level ownership: packing
   partitions by channel first, so every pack has exactly one owning channel. Decide where the
   partition is actually made (`self.packer`'s plan, or a pre-pass over the unloaded items), what
   carries the owner (a field on the pack, or a parallel map), and how `_release_to_stock`
   (`:442-495`) consumes it instead of resolving `sku` against `self._originals`. `CONTEXT.md`
   currently says "The pack plan is fixed by the trailer's full contents" — that entry needs the
   channel-partition amendment, and this is probably an ADR (hard to reverse, surprising without
   context, a real trade-off against mixed packs).

4. **The crew gang.** `_unload_merged` / `_unload_split` already hold one `dock.crew_size` gang,
   which the sizing inventory calls "already the right shape for a shared crew" — confirm that the
   door-team cap (`PHASE2_DOOR_TEAM`, inbound-optimization 28) still means what it meant when the
   trailer is mixed.

Flag-off must stay byte-identical: whatever this becomes, a single-channel or inbound-off run walks
the v1 path unchanged.

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§1. Re-resolve its line numbers before trusting one.

## Answer

**A `SiteReceiving` coordinator in `Inbound/receiving.py`, holding the one dock, the one yard and
the yard-drain record, reaching each leaf through exactly two public ports.** The manager keeps
every piece of state that is genuinely a leaf's; nothing merchandise-bearing moves.

### Anchor drift

The ticket's numbers are 1-2 low. Re-resolved at this session's HEAD: `_receive_standing`
`inventory_reorder.py:708-833`, `_unload_merged` `:835`, `_unload_split` `:881`,
`_release_to_stock` `:442`, `check_reorders` `:1002-1050`; the single call site is
`strategy_runner.py:1359`.

### Two findings that dissolved sub-question 3

1. **A pack can never be mixed.** `YardTransit.planned_lots` (`Inbound/transit.py:422`) returns
   `(sku, qty)` **per contiguous lot**, and a `LoadPlan` (`Inbound/pack.py:60`) holds exactly one
   `order`. Packing is already per-SKU, so the charter's "PACKING partitions by channel before it
   packs" is true *by construction*. There is no partition to build, and no trade-off against
   mixed packs to weigh.
2. **The owner needs no field.** `regime_of` (`Warehouse/kernel/regime.py:22`) duck-types over
   Order, StorageUnit and `.order`, single-valued per entity (CLAUDE.md §2's reuse list). Every
   unloaded item already answers its own channel. Nothing is carried, stamped or mapped at the
   unit level.

Consequence: **`_release_to_stock` (`:442`) does not change at all.** The sizing inventory called
it "the hard break"; under a coordinator it is untouched, because it sits on the lead-queue path,
which standing mode structurally no-ops (`YardTransit.release` returns `[]`,
`Inbound/transit.py:384`) — and because nothing ever hands a mixed trailer to a manager.

### 1. The object, and its seam

The drain's four documented steps split cleanly on manager contact: **steps 2 and 3 (the door fill,
the unload) touch no manager state whatsoever** — only `dock` and `transit`. Only step 1
(plans-at-arrival) and step 4 (the canonical handoff) reach a leaf. So the seam is two ports,
matching those two steps:

- `plan_lot(sku, qty) -> list[PutawayItem]` — resolve `_originals`, apply `inbound_split`, pack,
  `_stamp`, debit the pack shortfall against `_deferred_qty`.
- `accept(item, t0, dur, w) -> None` — the deferred-to-queued flip, `_queue`, `_recv_seconds`.

Both are new **public** methods on the reorder mixin. ~220 lines of drain behind two methods: a
deep module, and the privates stay inside `Warehouse/`.

**Rejected: electing one manager to run the drain for both.** The elected leaf's `_originals`,
`_putaway_seq` and ledger are still *its own*, so the routing problem survives intact and an
asymmetry nothing in the model justifies is added on top; every site-scoped artifact
(`_yard_drains`, `dock.records`) would then live on one arbitrary leaf, which ticket 03 would have
to un-pick.

### 2. Where it lives

**`Inbound/receiving.py`.** The import boundaries make the discipline structural rather than
conventional: `forbid: [inbound, wh_inventory]` (`context/architecture.yml:100`) means the
coordinator **physically cannot** reach a manager private — it duck-types two manager-shaped
objects and calls the two ports. `regime_of` is `wh_kernel`, which `Inbound/` may import
(`Inbound/README.md`: value layers only), so step-4 routing is legal from there.
`Inbound/README.md` already states the contract the ports formalise: *"packs cross the seam
through the manager's `_queue` entry"*.

Rejected: `Optimization/simdriver/` (no boundary problem, but it buries domain physics where
`Tests/unit` does not reach and where the package READMEs say it does not belong);
`Warehouse/inventory/` (dead — `forbid: [wh_inventory, inbound]`, `:103`, blocks the dock and
transit types it would need).

### 3. Which inbound modes couple

**The standing yard only.** Of the three transits — `_BatchTransit` (no inbound), `_TrailerTransit`
(v1, `strategy_runner.py:1110`) and `_YardTransit` (standing, `:1075`) — the campaign runs
standing, and `_TrailerTransit` exists as the verification bridge. Coupling both would mean
building owner routing twice, in two unrelated drains (`_receive`'s dock-deque branch,
`:685-704`, is a separate ~20-line path), for a mode no experiment uses.

A coupled run **requires** `standing` and **refuses at construction** when handed a non-standing
transit. A loud refusal, never a silent fallback to per-leaf receiving: memory
`pool-run-swallows-dead-arms` — a silent degradation here produces a plausible-looking run that
answers a different question.

### 4. The phase order, and who drives it

`check_reorders`'s own docstring invites this: *"Each step above is a method so a future caller can
drive them separately."* So: **the coordinator gets `drain(put_deadline, recv_deadline, now_s)`
which drives the managers' phase methods in the site interleave, and `check_reorders` stays
untouched as the single-channel composition.** Two compositions, one set of phases.

The interleave: phases 0-3 (tick, reclaim, advance, fire) for **both** leaves, then one shared
receive, then phase 5 (the put drain) per leaf.

**The amendment to the charter's sketch:** phase 3 (`_release_arrivals`) still runs **per leaf**,
even though it is a structural no-op in standing mode. It is the phase that lands trailers in the
yard (`YardTransit.release` does the whole calendar and returns `[]`), so it must run for both
leaves *before* the shared receive. Dropping it because its return is empty would strand every
arrival.

**The test: a second canonical sequence in `Tests/unit/test_reorder_phases.py`, not a new file.**
The site interleave is a claim about the *same* seven phases, and that test's entire value is that
every lawful order is written down in one place where a reordering fails. The per-leaf `PHASES`
tuple stays; a `SITE_PHASES` interleave joins it.

### 5. Pack routing at the release seam

Given findings 1 and 2, one decision remains: **a `{sku: leaf}` dict built once by the coordinator
at setup from the two managers' order sets, and `regime_of` everywhere else.** Step 1 resolves the
lot's owner from the dict (it holds only a bare sku, before any unit exists); step 4 routes by
`regime_of(item.unit)`.

**Assert disjointness when building the dict.** An overlapping sku means the channel filter
(`strategy_runner.py:769-771`) let one order into both leaves — a bug that would otherwise surface
as merchandise silently delivered to the wrong warehouse.

Stamp ordering is safe without further work: `_stamp` increments a per-leaf `_putaway_seq`, step 1
stamps in yard order, and each leaf's put queue only ever compares its own seqs — so per-leaf FIFO
is preserved by the canonical handoff without any cross-leaf sequencing.

### 6. The crew gang and the door-team cap

**Both unchanged, and the cap means more than it did.** `_unload_merged`/`_unload_split` hold one
`dock.crew_size` gang and are entirely channel-blind — they deal `item`s and never look inside one.
`cap` (`PHASE2_DOOR_TEAM`, inbound-optimization 28) is read once at `:786` as trailer physics: "at
most `cap` receivers can support one trailer's unload and pack at once" is a statement about a
physical door, which does not care whose merchandise is inside.

What changes is the crew it caps. Today each leaf fields the **whole** site receiving crew
(`workunits.py:366-369` — the double count), so `cap` was limiting a gang already 2x oversized.
Under one dock it caps the real crew. That is a correction, and it belongs in the published
comparability caveat the map already carries, not in a code change here.

### 7. What each manager keeps

| Moves to the coordinator | Stays on each leaf |
|---|---|
| the `Dock` (one), the `YardTransit` (one) | `_originals`, `_putaway_seq`, `put_queues`, `_held` |
| `_yard_drains` (the per-drain contention/cut row) | `_deferred_qty`, `_queued_sku_counts`, `_queued_qty` |
| `dock.records` / `.seconds` / `.unloaded` / `.cut` | `inbound_split`, `packer`, `_recv_seconds` |
| the ctx freeze, door fill, unload, handoff order | `_now_s`, `_stock_queue`, `_bin_pref` |

Consequence: `mgr._dock` and `mgr.transit` are **`None` on both leaves** during a coupled run, so
`dock_depth` (`Inventory_Management.py:1168`), `recv_snapshot` (`:1190`), `transit_snapshot`
(`:648`) and `in_transit_qty` (`:626`) would all read zero on a leaf.

**Those accessors REFUSE on a coupled run rather than returning zero.** A leaf reporting
`dock_depth == 0` while the site dock is backed up is exactly the silent-wrong-number class this
repo keeps getting bitten by (`a-right-site-total-hides-two-wrong-shares`,
`free-bins-counts-the-whole-geometry`). Ticket 03 decides where the site-scoped rows land; this
ticket's job is to make reading them from the wrong scope impossible in the meantime.

### 8. Receiving seconds

**Both scopes, and the site total is the authority.** `accept()` credits the owning leaf's
`_recv_seconds` with `dur`, so the two leaves sum exactly to the site total and today's reader
(`Performance_Evaluations/tables/per_run.py:89`) keeps working unchanged. The coordinator holds the
authoritative site total plus the gang's wall time.

These are **not** the same number and must never be reported as though they were — memory
`a-right-site-total-hides-two-wrong-shares` is precisely this failure mode, and
`calendar-span-is-not-work-days` is why the denominator matters. Ticket 07 owns the band that reads
it; this ticket owes it a number honest about its own scope.

### 9. What proves flag-off byte-identical

**A one-leaf coordinator test, not a flag test.** A coordinator handed exactly one manager must
reproduce `_receive_standing` byte-for-byte: the same seeded single-channel standing-yard run
driven (a) through `check_reorders` and (b) through the coordinator with one leaf, asserting
identical `dock.records`, identical `_yard_drains` and identical put-queue contents per batch.

That is a stronger and cheaper claim than "the flag is off", because it exercises the new path and
pins it to the old result. The trap, from memory `lockstep-tests-compare-aggregates-only`: assert
the **record sequences**, never their sums.

### 10. The ADR, and the CONTEXT.md amendments

**One ADR, on the coordinator and its seam — not on pack routing.** Roughly *"Site receiving is a
coordinator above both managers"*: hard to reverse (it re-shapes the drain, the phase driver and
the leaf accessors), surprising without context (a reader will ask why receiving left the manager
when ADR-0003 put put-away decisions there), and a genuine alternative (the elected manager) was
rejected for stated reasons. Pack routing gets a sentence *inside* it — "no owner field is needed,
`regime_of` already answers it" — rather than an ADR of its own, because finding 1 removed the
trade-off an ADR exists to record.

Two `CONTEXT.md` edits, **neither the one the charter predicted**:

- **Packing** — amend to state the *consequence*, not to add a partition: the pack plan is fixed by
  the trailer's full contents, and because it is built per contiguous SKU lot, every pack has
  exactly one owning channel. A mixed trailer never produces a mixed pack.
- **Site receiving** — new term: the one coordinator that drains the site dock for both channels'
  leaves, owning every decision and holding no merchandise.

"Leaf" stays as it is: already in use across the maps, and it conflicts with nothing in the
glossary.

### What this ticket did NOT decide

The **site space view**. `_receive_standing:740` freezes `ctx.space` from a `SpaceTimeline`
documented as *"one per arm"* (`Inbound/space.py:118`) and attached to one manager
(`strategy_runner.py:1096`), so under one yard a mixed trailer would be ranked on half the site's
free space — the per-leaf artefact this map exists to kill. `freeze(mgr, epoch)` takes one manager
and is pinned pure by `Tests/unit/test_space_timeline.py`, so this is a real design cost and not a
signature tweak. Graduated as
[Design the site space view](08-design-the-site-space-view.md), blocked by this ticket.
