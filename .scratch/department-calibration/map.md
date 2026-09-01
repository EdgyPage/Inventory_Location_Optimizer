# Department calibration

Label: wayfinder:map

## Destination

Baseline staffing and expected throughput as declared, committed configuration: the
calibrated era — one site day (a calendar day with a declared stretch of production time
inside it, every crew on the same boundary), throughput in equilibrium, deliberate headroom
so optimization decisions have something to move — landed on `develop` with its record
riding every config seam, so the inbound funnel (and any later campaign) runs under a
regime someone chose rather than one the defaults inherited.

## Notes

- **Execution override: ON** (the family precedent — both inbound maps carry it, and this
  effort's deliverable is literally configuration loaded into the config files). Once a
  ticket's governing decisions close, implementation graduates from fog into `task` tickets.
- **Charter — settled during charting (2026-09-01), binding on every ticket:**
  - **The day model.** A day is a calendar day with production time inside it — one
    boundary for the whole site, every crew on it. Site state persists across it
    (glossary: *Working day*, amended this session).
  - **The denomination invariant.** A department's capacity is denominated in the same
    unit as the work that arrives at it — against the day, never per batch. A per-batch
    grant scales with batch cadence, not with time, and drifts silently whenever a batch
    outlives a day (glossary: *Duty cycle*, added this session). This is the invariant the
    pilot's first attempt violated: the dock ran at a ~5% duty cycle against store picking
    and nobody had chosen that.
  - **Equilibrium with headroom.** Calibration keeps throughput in equilibrium — each
    day's work fits the declared shift, within bounds — while deliberately NOT staffing to
    the maximum: scarcity is what lets optimization decisions actually affect operations.
    The headroom is a declared number, not an accident.
  - **Knowability.** Batches are precomputed and reorders derive from them, so the total
    work is knowable ahead of a run: demanded units and handling mass are computable from
    the script alone (the dock's demand fully — unloads have no travel term); pick and
    put-away TRAVEL is arm-dependent and needs a measured run. Calibration is therefore
    hybrid by nature; the split is exact (see the assets survey, `arm_invariance`).
  - **The inbound campaign holds.** Phase 2 was already held at the pilot resolution;
    whether phase 1 also waits for the calibrated era is
    [Sequence the inbound funnel](issues/05-sequence-the-inbound-funnel.md), not settled
    charter.
- **The mechanism largely exists, dormant** — this map is mostly calibration plus one
  missing seam, not new physics: `SHIFT_DRAIN_OR_CAP` (off by default) forces the pick cut
  on, gives receiving the SITE's day, and closes each day with the drain-or-cap ledger;
  `releases_per_day=N` paces batches into fixed day slots with `released_late` recording
  every second of lag. Under pacing, the dock's day-remainder-per-batch grant becomes
  CORRECT (a batch no longer outlives its day). The picker count is the missing seam: no
  CLI flag, no `CONFIG['global']` key, no run-spec record — store=25 / ff=20 are
  compile-time constants. Full survey with file:line anchors:
  [assets/calibration-facts.md](assets/calibration-facts.md).
- Memories every session should load: `config-knob-has-five-seams`,
  `receiving-is-its-own-crew`, `one-clock-one-speed-one-config`,
  `working-day-clock-plan-corrections`, `config-is-not-a-channel-to-an-evaluation`,
  `growth-ladder-use-the-skus-knob` (all in `context/memory/store/`).
- Skills: `grilling` + `domain-modeling` on every HITL ticket; `codebase-design` on the
  seam/record tickets.
- Prior art: the pilot gate resolution
  (`../inbound-optimization/issues/22-run-the-pilot-gate.md`) carries the measurements
  this map exists to answer — the 19-working-day store batch against a one-day-per-batch
  dock grant, effective receiving capacity `crew × day / 2` per batch, the 7.4× receiving
  load spread across the four leaves against one global crew knob, and the measured
  duty-cycle numbers. The inbound map's Out-of-scope entry is this map's birth record.
- Root `CONTEXT.md` already carries the settled terms (*Working day* amended, *Duty
  cycle* added, 2026-09-01).
- Tracker conventions: `docs/agents/issue-tracker.md` (Wayfinding operations).

## Decisions so far

<!-- one line per closed ticket: gist + link -->

## Not yet specified

- **The builds** — every implementation graduates here once its governing decisions close.
  Already visible through the fog: the PICKER STAFFING SEAM (the one department with no
  runtime knob at all — CLI flag, `CONFIG['global']` key, spec accessor on the
  `recv_crew_spec` pattern, run-spec record + both restore sites, `workunits._shared`);
  the STAFFING RECORD build (shape decided by
  [Design the staffing record](issues/03-design-the-staffing-record.md), including the
  `put_crew_spec` no-CONFIG-key trap fix riding along); the ERA WIRING (whatever
  [Define the calibrated era](issues/01-define-the-calibrated-era.md) decides about
  `SHIFT_DRAIN_OR_CAP` / `releases_per_day` defaults for campaign specs); and the
  CALIBRATION RUN itself with its verification read (procedure from
  [02](issues/02-choose-the-calibration-procedure.md), bands from
  [04](issues/04-declare-the-equilibrium-bands.md)).
- **Measured-vs-expected throughput as a reported check** — once expected throughput is a
  declared config record, an evaluation comparing the run's realized throughput against
  its declaration (and its duty cycles against their bands) is the natural audit; needs
  the record to exist first, and the analysis-side stamp rides
  `config-is-not-a-channel-to-an-evaluation`.
- **Interaction-effects reporting beyond the bands** — the user's framing names
  "interaction effects between departments"; the bands capture equilibrium, but how the
  coupling itself is surfaced (receiving throttles put-away throttles availability
  throttles picks — a lag/propagation read, not just levels) is dim until the era and
  bands exist.

## Out of scope

- **Real-world staffing recommendations.** Arrivals are batch-quantized, so the modelled
  crew faces its whole day's work at once and its makespan reads LONG (memory:
  `receiving-is-its-own-crew`) — this map declares SIMULATION regimes so comparisons are
  apples-to-apples; it does not size an actual dock crew.
- **Lead-time denomination in seconds.** Converting `LEAD_TIME_UNIT` from batches moves
  every restock result on every arm and is a change of its own (`Inbound/dock.py` records
  it); the calibrated era works with batch-quantized arrivals as they are.
- **Staffing as a swept experimental axis.** This map declares ONE calibrated baseline per
  channel; sweeping crew sizes as a campaign (what does a fifth receiver buy?) is a later
  effort that would start from the record this map creates.
