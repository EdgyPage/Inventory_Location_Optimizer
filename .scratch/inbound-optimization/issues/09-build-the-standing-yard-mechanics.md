# Build the standing-yard mechanics

Type: task
Status: resolved

## Question

Build everything "Design the standing-dock mechanics" (01) decided — its `## Answer` is the
spec; this ticket exists so the build is claimable work. The pieces:

- `INBOUND_STANDING_YARD` flag (default off; loud failure when set without a trailer type;
  five-seam propagation including `workunits._shared`).
- `YardTransit(TrailerTransit)`: standing yard, real doors, calendar-only `release()`,
  plans-at-arrival (manager-side packing into `Trailer.plans`), deferred-until-unload ledger
  with the per-unit flip, yard-arrival stamps, the standing-work surfaces `_receive` drives.
  `TrailerTransit` / `BatchTransit` bodies untouched — byte-identity by construction.
- Door-team crew allocation: `INBOUND_CREW_ALLOCATION` ('split' default / 'merged' bridge),
  the `allocation.partition` deal in dock-priority order, the `crew_clock` subset extension,
  the three-step reassignment rule, non-budget-gated door-fill at the top of `_receive`.
- Canonical merged handoff order to `_queue`, whatever the allocation.
- The yard/dock registry split in `Inbound/priorities.py`: additive `INBOUND_YARD_POLICY` /
  `INBOUND_DOCK_POLICY`, both seeded 'fifo'; the existing ordering bound covers both; global
  registry and knob untouched.
- Unload-cost coefficient knobs (`INBOUND_UNLOAD_INTERCEPT` / `_WEIGHT_COEF` / `_VOLUME_COEF`),
  defaults by-reference to `PutawayCost` — archive untouched at defaults.
- Census/cut/stamp extensions: `depth` / `merchandise()` / `snapshot()` over yard + staged
  remainders; `dock.cut` = staged remainders at the whistle; `arrived_s` / `staged_s` /
  `emptied_s` on the trailer.
- Tests: existing 12 pipeline tests green; the degenerate lockstep (merged = byte-identical DB
  vs v1); the containment test (split = identical DB except dock work_events t0/worker); unit
  pins for the reassignment rule and the zero-budget door-fill. `_lot` to yard renames ride
  along (mind the case-only-rename trap if any page name shifts).

## Answer

Built and committed on develop, 2026-08-29: `64b2d31` (feat(inbound): the standing yard —
real doors, door teams, labor-only allocation).  Every bullet of the question landed as
specified by "Design the standing-dock mechanics" (01); the deltas worth recording are at
the end.

**What exists now, and where:**

- `INBOUND_STANDING_YARD` (default off) plus the family it gates —
  `INBOUND_CREW_ALLOCATION` ('split' default / 'merged'), `INBOUND_YARD_POLICY` /
  `INBOUND_DOCK_POLICY` (both 'fifo'), `INBOUND_UNLOAD_INTERCEPT` / `_WEIGHT_COEF` /
  `_VOLUME_COEF` (None = PutawayCost by reference) — declared in `settings.py`, threaded
  into `CONFIG`, and riding `inbound_spec()` which already crosses `workunits._shared`,
  so the worker-payload seam is wired.  CLI flags and run-spec recording stay DEFERRED to
  the first sweep, the inbound family's own recorded precedent ("CLI flags arrive when
  first swept") — the funnel campaign is that sweep.
- Loud failures at spec build: standing without a trailer type (the ticket's named
  contradiction) AND standing without a receiving crew (added: a yard nobody can unload
  defers merchandise forever with nothing raising — the same silent death, so the same
  guard), plus an unknown allocation string, rejected at both the spec and the
  constructor.
- `YardTransit(TrailerTransit)` in `Inbound/transit.py`: `STANDING = True` is what
  `_receive` probes (the other transits satisfy the standing surfaces trivially by not
  having them — neither was edited).  `release()` is pure calendar (arrivals join the
  yard event-stamped with `arrived_s = dispatch + lead`; nothing is delivered); the
  standing surfaces are `unplanned`/`planned_lots` (plans-at-arrival), `freeze_ctx`/
  `yard_order`/`dock_order` (drain-frozen rankings), `stage`/`door_freed`/`discard` (the
  door lifecycle), and census overrides (`depth`/`merchandise()`/`snapshot()` over yard +
  staged remainders, pending-aware).  The yard is kept in (arrival stamp, seq) order —
  the charter's entry order; `stage` raises past `doors`.
- `_receive` reroutes whole to `_receive_standing` (manager-side, `inventory_reorder.py`)
  when the transit is standing: plans fix at yard arrival per contiguous lot (the exact
  portions v1 packs, via the manager's packer and `_originals`; units stamped THEN, so a
  pallet standing three batches is three batches old); the door fill is
  whistle-independent; the unload is budget-gated per the allocation mode; the canonical
  merged handoff (trailers by dock rank, units by local rank, filtered to what unloaded)
  performs the PER-UNIT deferred→queued flip; `dock.cut` re-counts staged remainders at
  the whistle and the yard is never cut.  `Inventory_Management._stamp` was split out of
  `_admit` so the stamp still has exactly one home.
- Door teams: `allocation.partition` deals the crew across staged trailers in
  dock-priority order (cycling, top ranks take the extras); `crew_clock` grew
  `earliest_subset`/`can_start_subset`/`charge_subset` (a team of everybody IS `charge`);
  `Dock` grew `team_next_free`/`can_start_team`/`charge_team`.  The split loop advances
  whichever team can start soonest, so doors free STAGGERED and every reassignment sees
  every earlier emptying: (1) top-ranked staged trailer with no workers, (2) own door's
  replacement, (3) top-ranked with fewest workers.  'merged' is the v1 pooled gang kept
  as the verification bridge.
- The registry split in `Inbound/priorities.py`: `YARD_POLICIES`/`DOCK_POLICIES`
  additive, both seeded 'fifo' (arrival stamp, seq tiebreak via the stable sort);
  `INBOUND_GLOBAL_POLICY` and the global registry untouched and unread in standing mode;
  the one `bounded_order` bound covers both new rankings.  `DockContext.lot_depth`
  renamed `yard_depth`; the `_lot`→`_yard` identifier/docstring renames rode along.
- Stamps: `arrived_s`/`staged_s`/`emptied_s` live on the trailer (absolute-clock,
  event-accurate: a refill's `staged_s` is the epoch + crew-clock offset of the emptying
  that freed its door).  Because a trailer object is dropped when it empties (the
  LoadPlan-retention lesson), `YardTransit.stamps` keeps one
  `(seq, arrived_s, staged_s, emptied_s)` tuple per finished trailer — the raw material
  "Define the yard metrics" (07) reports from; column naming and surfaces stay that
  ticket's work, as decided.

**Byte-identity, all four layers proven:**

1. Flag-off binds the v1 classes untouched; the full pre-existing suite passes (1,780).
2. Degenerate lockstep: standing + FIFO + doors ≥ everything + no cap + 'merged' writes
   a run DB **byte-identical** to the v1 drain through the production seam
   (`Tests/e2e/test_standing_yard_e2e.py`; only `simulation_runs.created` — wall clock —
   is masked, proven the file's sole nondeterministic column by a two-identical-runs
   probe).  Stream-level lockstep at manager scale backs it in
   `Tests/unit/test_standing_yard.py` (records, queue stream, ledgers, censuses,
   carryover — drain by drain, never aggregates).
3. Containment: same config with 'split' is identical except the receive rows'
   t_abs/t_local/shift_index/actor_uid/actor_local.  Making this byte-true required one
   subtlety: `dock.seconds` accrues at the CANONICAL HANDOFF, not at charge time, so the
   float sum associates identically whatever the allocation (charge-order accrual
   differed by one ulp).
4. Capped runs relabel levels by design (v1's floor remainder is queued; the standing
   remainder is deferred) with flows conserved — pinned as a relabeling test, never an
   equality test.

**Tests added** (18 new): spec-guard pins (both loud contradictions, the allocation
enum, flag-off None, coefficient by-reference/override), pure-calendar + event-stamp
pins, yard-ordering pin, the two lockstep layers, the reassignment rule (steps 1 and 3,
separated deterministically by pack count — weight is log-compressed and cannot spread
trailers), the zero-budget door-fill, the consumed-index remainder with the per-unit
ledger flip, the capped-relabeling conservation pin, and the census extension.  All
existing tiers green: 1,796 passed (unit + integration + e2e).

**Deltas against the ticket text, all recorded above:** the second loud guard (no
receiving crew); seconds-at-handoff; the `stamps` drain surface (raw tuples only — 07
owns reporting); CLI/run-spec seams deferred by family precedent; and one pre-existing
v1 gap this build exposed and fixed — `lead_queue_depth`/`in_transit_qty` read
`BatchTransit._entries` through the `_lead_queue` shim and broke on any trailer-flag-on
run through the production seam; they now read the transit census
(`depth`/`merchandise()`), byte-identical flag-off.  Note for standing runs:
`batch_stats.recv_depth` reads the dock FLOOR (0 by design — the buffer is the
trailer); the standing backlog lives in the transit census until 07 decides its
reporting surface.
