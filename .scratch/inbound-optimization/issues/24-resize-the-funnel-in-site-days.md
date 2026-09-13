# Re-size the funnel in site days

Type: task
Status: resolved
Blocked by: ../../department-calibration/issues/13-derive-the-expected-travel-closed-form.md

AFK once unblocked. Cross-map gate: waits for the department-calibration map's
[Take the reference run](../../department-calibration/issues/09-take-the-reference-run.md),
which reports the wall cost of one site day under the era — the number the arithmetic below needs.
Must be resolved BEFORE phase 1 launches; it does not wait for
[Verify the derived receiving crew under arrivals](23-verify-the-derived-receiving-crew.md).

## Question

Phase 1 (136 work units) and phase 2 (480 units, ~13 h, ~1.1 TB) were sized in BATCHES at
published depth. Under the era's one-release-per-day pacing a batch IS a site day, and the funnel
inherits the reference run's window: 40 site days with days 20–39 measured (department-calibration
[Sequence the inbound funnel](../../department-calibration/issues/05-sequence-the-inbound-funnel.md),
decision 4). Re-estimate both phases against that window from the reference run's measured wall
and RSS per day, and record the new sizing on the campaign entry (archive-as-you-go stays
mandatory; the grids remain the trimming lever).

Two build items ride here, both in the selection writer / phase-2 launcher this map owns:
- **Pin the calibration record across phases.** `run_restock_selection` stamps the staffing
  record's identity into `restock_selection.json`, and `inbound_policies` REFUSES to start when
  the current derivation's identity differs — the era's twin of the existing `PHASE2_ARMS`
  refusal. Without it a `calibration_stale` re-derivation between phases makes the `inb_off`
  anchor's byte-identity check compare across derivations silently (decision 7).
- **The window is declared once.** Both specs (`inbound_select`, `inbound_policies`) take the
  40-day depth and the 20–39 measurement range from one place, so no campaign run reports
  utilization against a warm-up the constants never saw.

## Comments

2026-09-10, from department-calibration
[Declare the coverage against the inbound lead](../../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md),
decision 11: **phase 1 runs with the standing yard on under `fifo`.** The record now derives
each SKU's lead as its supplier lead plus the trailer's day-quantized transit (1.766 site days
at the pilot regime), and the floor is solved AT that lead -- so an inbound-off phase 1 and an
inbound-on phase 2 would field different floors, levels and warehouses, and the calibration pin
this ticket builds would refuse phase 2 as designed. The yard is slack under the derived crew
(23), so phase 1 with it on costs little, and both phases then share one record and one
warehouse. Two things for this ticket's build: `inbound_select` takes the same
`PILOT_RUN_DEFAULTS` arrival regime as `inbound_policies`; and the `inb_off` anchor's role needs
restating, since phase 1 is no longer an inbound-off run -- what its byte-identity check is
against is this ticket's to record. The regime itself (doors, median, spread) is 25's and must
be fixed before phase 1 launches, because median and spread now move the record.

2026-09-10, from resolving
[Decide the contention regime under the derived crew](25-decide-the-contention-regime-under-the-derived-crew.md):
the campaign now holds behind the site-dock coupling (map, Out of scope), and the sizing this
ticket owes changes shape with it -- a phase-2 cell becomes a PAIR of arms (one store rule, one
fulfillment rule) under one inbound policy, so the unit count is no longer `arms x policies`
per channel leaf. Re-estimate against the coupled run's wall and RSS per site day once it
exists; the calibration pin and the once-declared window are unchanged.

2026-09-12, from resolving
[Re-run the gate and fix the fee threshold](29-rerun-the-gate-and-fix-the-threshold.md): **the
gate PASSES, so phase 1 is launchable and this ticket's sizing is what stands between here and
the launch.** Two things it hands over.

**The coupled run this ticket was waiting for now exists.** The comment above says to re-estimate
against "the coupled run's wall and RSS per site day once it exists" —
`comparison_20260912_134002` is a coupled 40-day run at `--spec inbound_pilot` on the reference
pair, and it is the campaign's own regime (v3, era defaults, derived crew 23). Size against it.

**This ticket is denominated in SITE days and the fee knob is in CALENDAR days, and they differ
by exactly 3.** 29 lost a session to that confusion: a sweep recorded in site days was about to
be committed into a knob consumed in calendar days, which would have left the fee axis
identically zero. Both units are legitimate here and this ticket touches both — a batch IS a
site day (28,800 s, `timeline.DEFAULT_SHIFT_SECONDS`), so the 40-day depth and the 20–39
measurement window are site days; detention and `PHASE2_THRESHOLD_DAYS` are calendar days
(86,400 s). State which day every number is in, at every step. Do not inherit the bare word
"days" from the threshold work.

## Answer

RESOLVED 2026-09-12. **Both phases are re-sized against the coupled run, and phase 2 now fits on
one drive: ~169 GiB against the old 1.1 TB, 120 work units against 480.** Three builds landed
with it — the window and the arrival regime are each declared once, and the calibration pin has
two refusals. One of them fixed a defect that has nothing to do with sizing and would have
spoiled every inbound-on cell of phase 2; it is decision 4 below.

### 1. The measurement, and what it is of

Everything below is measured off `comparison_20260912_134002` — the passing gate run, which is
the campaign's own regime (v3, era defaults, derived crew, coupled, `--spec inbound_pilot
--n-batches 40`, one inventory pair, four coupled units, `--workers 2`). Its log and its
`runtime_metrics.db` carry per-leaf walls; the four units' start and finish come from the worker
timestamps and the sim DBs' mtimes (the run was interrupted and resumed, so two units' DONE lines
never drained — the mtimes are what close them out).

| what | measured |
|---|---|
| per-pair SETUP, before any worker starts | **3,633 s (60.6 min)** — line floor 689 s store / 569 s ff, fill 104 s, staffing 309 s, then 1,955 s of warehouse planning + batch precompute |
| per COUPLED unit, 40 site days, both leaves | 964 / 1,077 / 1,181 / 1,313 s — **mean 1,134 s, i.e. 28.3 s per site day** (24.1–32.8) |
| per-leaf sim loop inside that | store 670–1,084 s (mean 819), fulfillment 402–507 s (mean 455) |
| peak RSS per coupled unit | **3.2–4.1 GiB** (3,310 / 3,732 / 3,970 / 4,066 MiB) |
| disk per coupled unit | **~1.4 GiB** (store 459 MiB + fulfillment 924 MiB, sim + keyframes) |
| shared per (cell, pair) | 52 MiB — planned inventory, warehouse, batch pickles |

Two things this replaces. The old basis was "~1,230–1,320 s wall at ~5.5 GB peak RSS per unit at
published depth": a coupled unit now runs BOTH leaves in about that same wall and at **less** RAM
than one old leaf, because it builds one warehouse for the site rather than one per channel. And
the old 136/480 unit counts assumed **two** inventory pairs; the calibrated era's reference
catalogue is the one-pair `mixed_realistic_bell_lt0` view, so every count below is per inventory
pair and the totals are stated at one.

### 2. Phase 1 — `--spec inbound_select`

**68 leaf work units** (34 arms x 2 leaves x 1 pair), was 136. Uncoupled, so a unit is one leaf.

- **Wall: 12.0–16.8 h of unit-seconds**, plus the 1.0 h per-pair setup once. The floor is the
  measured leaf sim loops (34 x (819 + 455) s); the ceiling adds the ~250–270 s of worker-local
  setup observed on the coupled workers, which uncoupled each leaf pays for itself rather than
  sharing. At 6 workers that is ~2–3 h wall; at 2 workers, ~7–9 h.
- **Disk: ~47 GiB.** RSS is the one number that goes UP per unit-slot, not down: an uncoupled
  leaf builds its own warehouse, so budget near the coupled unit's 3.2–4.1 GiB per worker.
- **The bracket is honest and not reducible from this run.** No uncoupled leaf has been timed
  under the era at all. Beyond the extra warehouse build, a phase-1 leaf fields the WHOLE derived
  site put and receiving crew (site-dock 19), so under drain-or-cap it cuts less and completes
  more per day — by an amount that differs per arm. That moves the wall in an unmeasured
  direction. It is also why the ceiling, not the floor, is the number to plan against.

### 3. Phase 2 — `--spec inbound_policies`

**120 COUPLED units** (10 cells x 6 rule pairs x 2 stock modes x 1 pair), was 480 leaf units. The
unit count falls 4x: half from the single pair, half from coupling fielding two leaves per unit.

- **Wall: 37.8 h of unit-seconds as a FLOOR** (120 x 1,134 s), plus the freeze. At 6 workers,
  ~6.3 h; at 2 workers, ~19 h.
- **Disk: ~169 GiB** (120 x 1.4 GiB + 10 x 52 MiB), against 1.1 TB. Archive-as-you-go stays
  mandatory by policy, but the campaign no longer needs a drive swap mid-run.
- **RAM: 3.2–4.1 GiB per worker.** Still RAM-bound before CPU-bound; at ~4.5 GiB budgeted per
  worker, 6 workers is ~27 GiB.

**It is a FLOOR and the word is load-bearing. Two costs are unmeasured, both cheap to measure,
and one of them is unbounded on paper.**

1. **The gain evaluator has never run in a timed cell.** The gate ran `yard_policy='fifo'`, which
   prices nothing. Eight of phase 2's ten cells run a gain bundle that prices every trailer
   against the arm's own machinery at every drain, and `fsight_wall` re-aggregates the whole
   remaining script once per DRAIN — `_window_rates`, O(n^2 . |batch|) multiplied by drains per
   batch, recorded on [Extend the gain bundles](20-extend-the-gain-bundles.md). Multiplying 37.8 h
   by an unknown is not an estimate.
2. **The per-cell reshape is unmeasured**, because the gate was single-cell. On a multi-cell run
   the 3,633 s setup is paid ONCE at the freeze and each of the ten cells then reshapes the frozen
   inventory. Bracket: +1.0 h (freeze only) to +10.1 h (if a reshape cost a full setup).

Both fall out of one two-cell probe, and that probe is now
[Measure what a gain cell actually costs](31-measure-what-a-gain-cell-costs.md) — not a phase-1
blocker, but the thing that turns this floor into a number.

### 4. The five decisions

**1. The window is declared once, in SITE days.** `whatif_config.CAMPAIGN_DEPTH_DAYS = 40` and
`CAMPAIGN_WINDOW_DAYS = (20, 39)`. The depth rides `run_defaults` on all three campaign specs, so
`--spec inbound_select` and `--spec inbound_policies` are each the whole launch and the two phases
cannot be typed to different depths. That is not cosmetic: the staffing derivation reads the
SAMPLED SCRIPT, so two depths are two derivations of one catalogue. The reading side gets
`equilibrium_report.py --window campaign`, which resolves to the constant instead of a hand-typed
`20-39`. Every declaration says which day it is in, and
`Tests/unit/test_funnel_window.py::test_the_campaign_day_and_the_fee_day_are_three_fold_apart_and_both_declared`
asserts the site day and the calendar day against their OWN declarations
(`timeline.DEFAULT_SHIFT_SECONDS` and `units.SECONDS_PER_DAY`) so a change to either fails here.
`PHASE2_THRESHOLD_DAYS = 0.40` calendar days is 1.2 site days, which is asserted for the same
reason: the bare number reads as nonsense until the unit is named.

**2. The arrival regime is declared once, and it rides the RUN on all three specs.**
`INBOUND_ARRIVAL_REGIME` (trailer, standing yard, doors, door team, lead median and spread) is
lifted out of `PILOT_RUN_DEFAULTS`; `PILOT_RUN_DEFAULTS`, the new `PHASE1_RUN_DEFAULTS` and
`PHASE2_RUN_DEFAULTS` all compose from it, and a test asserts it agrees key-for-key with what the
inbound axis's on-cells carry.

**3. Phase 1 runs the arrival regime ON, and coupling stays OFF.** This is the 2026-09-10
comment's "`inbound_select` takes the same `PILOT_RUN_DEFAULTS`" — read correctly, which it could
not be when that comment was written. `PILOT_RUN_DEFAULTS` gained `couple_channels` two days later
(26), and phase 1 may not couple: it is the ranking run, each channel needs its own hours scale,
and `run_restock_selection.select` REFUSES a coupled root. So phase 1 takes the same ARRIVAL
REGIME and not the coupling, which is exactly what splitting the constant buys. Decision 11's
reason is untouched: both phases now solve their line floor at the same order-to-shelf lead. The
per-leaf yard phase 1 thereby fields is the artefact 25 retired, and it rides along harmlessly —
phase 1 publishes no yard reading.

**4. The regime rides phase 2's RUN as well as its axis, and that is a DEFECT FIX, not
tidiness.** A multi-cell run freezes its inventory once per pair, before any cell starts, and
since department-calibration decision 11 that freeze reads the trailer's lead law: each SKU's
order-to-shelf lead is its supplier lead plus the day-quantized transit
(`sim_config.inbound_lead_law` -> `era_coverage.lead_block`) and the line floor is SOLVED at it.
Phase 2 named the regime only on its inbound axis. It would therefore have frozen a warehouse
stocked for **transit 0** and simulated nine of its ten inbound-on cells against it — logging a
transit of `0.000` that nothing compares with the cells', so the campaign would have published a
yard comparison run on stock sized for no yard. `scenario.py`'s own comment ("no yard or dock
decision is reachable from here") is still true and is exactly why this stayed invisible: the
freeze makes no yard decision, it reads a LEAD. With the regime at run level the freeze plans at
the same lead the cells simulate at, and `inb_off` becomes a better control than it was — same
warehouse, same stock, no yard, so the cell varies the pipeline and nothing else.

Two stale claims went with it. `phase2_inbound_axis`'s inline comment on the anchor still said
`inb_off` "is comparable with phase 1"; it is not, and now for two reasons rather than one
(phase 2 couples every cell — site-dock 06 section 3 had already rewritten the docstring above it
but not the comment beside the anchor — and phase 1 now runs the regime ON). The file header's
"phase 1 is a FRESH, inbound-OFF run" went the same way. **What the `inb_off` anchor is, recorded
as this ticket was asked to:** the inbound-off pole INSIDE the coupled model, over the same frozen
warehouse as its nine siblings — the control for "does running an inbound pipeline at all change
the answer, versus which policy runs it", and the only cell with no yard, which makes it the
structural zero for every yard quantity. It is not a cross-phase byte-identity check, and there is
nothing left in the campaign that is one; the cross-phase guarantee is decision 5.

**5. The calibration pin has TWO refusals, because one cannot do the job.** The rationale the
2026-09-10 comment gave has dissolved and a stronger one has replaced it. There is no calibration
RECORD any more (department-calibration's "Derive the expected-travel closed form": the derivation
is a pure function computed at setup from the run's own catalogue and geometry), so
`calibration_stale` is not the hazard. The hazard is the funnel's own design: **the campaign puts
a BUILD between the phases on purpose** — no phase-1 number is ever published, which is what makes
that legal, and [Extend the gain bundles](20-extend-the-gain-bundles.md) is exactly such a build.
A change that moves the derivation leaves phase 2 executing phase 1's ranking against different
crews, a different line floor and different levels, and nothing in the run tree joins two run
roots to notice.

- `run_restock_selection` stamps `staffing.pin` (a 12-hex digest per pair) and
  `staffing.projection` (the readable block a refusal is diagnosed from) onto
  `restock_selection.json`, and prints the line to copy.
- `whatif_config.PHASE2_STAFFING_PIN` is the copied digest, `None` until phase 1 has run.
  `validate_spec` refuses a `rule_pairs` spec that carries no pin — cheap, total, before a
  directory exists, the twin of the pairs refusal it sits beside.
- The MATCH cannot be checked there, because no derivation exists that early. The run stamps the
  declared pin onto its own `run_spec.json` and `workunits._check_campaign_pin` refuses, per pair,
  at the same seam that already compares a fresh derivation against a recorded one. A pin that
  does not NAME the pair is a refusal too: phase 1 ranked the pairs it ran.
- The digest is over a rounded PROJECTION (`staffing.pin_of`), not the derived block:
  reporting-only keys are outside it, so a change to what the record reports cannot refuse a
  campaign, and `PIN_SIGFIGS = 6` is where the float tolerance is declared — a digest of raw
  floats would be an `==` on floats by the back door. `script.batches` IS in it, so a depth
  mismatch is caught by the pin as well as prevented by decision 1.

### Landed

`Optimization/config/whatif_config.py` (the window, `INBOUND_ARRIVAL_REGIME`,
`PHASE1_RUN_DEFAULTS`, `PHASE2_STAFFING_PIN`, the spec key and the refusal, three stale claims),
`Optimization/simconfig/staffing.py` (`PIN_SIGFIGS`, `pin_of`, `pin_digest`),
`Optimization/run_restock_selection.py` (the stamp and the copy line),
`Optimization/run_simulation.py` (the run-spec stamp),
`Optimization/simdriver/workunits.py` (`_check_campaign_pin`),
`Diagnostics/equilibrium_report.py` (`--window campaign`),
`Tests/unit/test_funnel_window.py` (25 tests, new), and two updated assertions in
`test_era_wiring.py` / `test_funnel_spec_pairs.py`.

Gates: `Tests/unit` 2,387 passed; `verify_context`, `path_guard`, `docref_guard` and
`runschema.contract --check` all clean. Byte-identity: no committed spec outside the three
campaign specs gained a `run_defaults` key, `PHASE2_STAFFING_PIN is None` makes both refusals
inert until phase 1 runs, and `_check_campaign_pin` returns immediately when no pin is stamped —
which is every run that is not a phase-2 campaign.

### What is now between here and phase 1

Nothing on this map. The launch is `python -m Optimization.run_simulation --spec inbound_select
--profiles-dir <the one-pair reference view> --workers N` and nothing else; budget ~1 h of setup
then 12–17 h of unit-seconds, ~47 GiB.
