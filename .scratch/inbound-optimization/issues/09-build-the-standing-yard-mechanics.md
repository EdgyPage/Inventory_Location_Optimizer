# Build the standing-yard mechanics

Type: task
Status: open

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
