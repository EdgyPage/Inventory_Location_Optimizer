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
    **AMENDED 2026-09-06 (user decision, after the reference run ran):** travel is NOT
    measured. It is a closed-form expectation over the inventory's demand distribution and
    the warehouse geometry built at runtime, computed at setup per arm
    ([Derive the expected-travel closed form](issues/13-derive-the-expected-travel-closed-form.md)).
    There are **no calibration simulations**; the equilibrium check stays a REPORT.
    LANDED 2026-09-06: the expectation takes a placement DISTRIBUTION -- class-uniform for
    the pair's demand (one shared script; FIFO's long-run state), the arm's own initial
    placement for its report -- because a churning FIFO section migrates to the smear.
  - **The floor is a line, and the SKU carries its law** (decided 2026-09-06,
    [Choose the coverage floor](issues/15-choose-the-coverage-floor.md)): a SKU never holds
    less than one pick's worth of itself, floored SKUs run base-stock, the line distribution is
    stamped on the SKU and every closed form reads it -- no consumer re-derives a law. The
    drained clause judges labour only; the window verifies a steady state, never a wave.
    LANDED 2026-09-06 ([Build the line floor](issues/17-build-the-line-floor.md)); the 40-day
    check did NOT read in band -- the floor is not FIELDED on fulfillment (19) and not KEPT
    on the store (20), and overtime behind a labour-drained day raises the instrument (21).
    AMENDED 2026-09-07 ([Field the floor](issues/19-field-the-floor.md)): the floor is a
    PROMISE -- the warehouse is sized to hold it, the planner fields the REQUIREMENT exactly,
    a run that cannot refuses -- and the catalogue carries NO stock levels: stock is a run's
    declaration derived at setup in EVERY mode (ADR-0002). The era-only planner rule never
    existed; there is one planner contract.
  - **Demand is declared, the picking crew is derived, and the guarantee is per pick**
    (decided 2026-09-08,
    [Fit the store's window to its own steady state](issues/27-fit-the-store-window-to-its-steady-state.md),
    ADR-0004; REVERSES 01's "pickers are the one declared input"). Under the era NOTHING IS LOST
    -- a cut or unfilled pick is re-offered next day -- so every crew is sized on DEMANDED units.
    One declared scalar, the joint first-time confidence (0.95), replaces `rho_pick`: the line
    floor is solved so the stamped fill clears sqrt(c), the crew is the smallest integer whose
    expected cut share of units is under 1 - sqrt(c), both closed forms over the chosen inventory
    and the DECLARED day law (a Gaussian line count with a declared cv -- `settings.py`
    `*_BATCH_MEAN/STD`). Put-away and receiving keep rho; their backlog is reported. "Every day
    drained" is a reading, not a verdict. BUILT 2026-09-08 (29: demand declared, floor and crew
    solved, the regime decides which keys are inputs). The instrument split LANDED 2026-09-09
    (30: `supply` and `labour` clauses over the `carryover` flows and fresh demand; a capped day
    is a reading); the re-check (31) READ CLEAN 2026-09-09 -- both leaves in band on every clause
    at K = 31 / 23 and floors 1.273 / 1.267 -- so the era is CALIBRATED and its numbers are no
    longer provisional.
  - **No bespoke conversions implicit in the inventory.** A standing preference from the
    same decision: nothing authored on the catalogue may carry an implicit batch or day (the
    coverage-in-generation-batches trap of 09). Stock coverage is per SKU in days of its own
    expected demand; the method must scale to any item distribution unchanged.
  - **The inbound campaign holds -- LIFTED 2026-09-09.** Phase 2 was already held at the pilot
    resolution; whether phase 1 also waits for the calibrated era is
    [Sequence the inbound funnel](issues/05-sequence-the-inbound-funnel.md), not settled
    charter. The hold's condition (31 reads clean) is met; the inbound map's 23 / 24 may proceed.
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
- **A run is made small by its DECLARATION, never by a bin cap** (found while building
  [Field the requirement](issues/23-field-the-requirement.md), 2026-09-07). A `max_bins` /
  `max_aisles` cap that binds below what the levels need now refuses -- and it is self-defeating
  anyway: the coverage fixed point sizes the warehouse from the levels and reads the levels back
  off the geometry, so a smaller warehouse is a shorter trip, a higher derived lines/day, and
  BIGGER levels than the cap was containing. `--coverage-days` (with `--max-skus`) is the lever;
  the preflight canary and eight test fixtures were moved onto it.
- **Put-away fills an empty bin first** (decided 2026-09-07,
  [Let a base-stock top-up reach the shelf](issues/20-let-a-base-stock-top-up-reach-the-shelf.md),
  ADR-0003): a top-up consolidates into the SKU's own bin only when no empty bin fits, ahead of
  the rescues and of pending -- the new-bin decision is where optimisation happens. The rescues
  are receiving work (inbound does the repacking), priced and recorded; expected repacks are
  stamped zero. Picks drain a SKU's smallest bin first.

- **The free index slides, and the leaf total cannot see it** (found 2026-09-10,
  [Band the own-bin share and the free-index depth](issues/32-band-the-own-bin-share-and-free-index.md)).
  `batch_stats.free_bins` is the WHOLE geometry, so a leaf's reading includes the other channel's
  section; read per bucket off the record's `fielded` block. Under base stock a picked SKU holds
  two bins most of the time (remnant + empty-first top-up), so the setup headroom drains at the
  first-partial-pick rate toward a stationary fragmentation the record now DERIVES (34, landed
  2026-09-10: `fielded.fragmentation`); the planner's 0.85 fill WAS an assumed number standing
  where that closed form belongs -- 35 (LANDED 2026-09-10) derives the fill per bucket from it,
  floored at `min_headroom` (0.05, assumed); the reference warehouse is 2,536 aisles / 2,311,000
  bins now, a comparability break.

- **Every SKU sits at the line floor, in BOTH sections** (measured 2026-09-12 while working
  [Close the fulfillment fill-law gap](issues/38-close-the-fulfillment-fill-law-gap.md)):
  239,938/239,938 store and 160,062/160,062 fulfillment, `Q == L_s`, 100% of stamped demand each.
  `coverage_days`, the lead and `safety_days` bind on NOTHING -- the entire declared level is
  `floor_lines * E[line]`, ~1.5 lines per SKU -- so the fill law reduces to one event (a second
  line inside the replenishment lead) and `floor_lines` is the only lever the solve has. Read
  `floor_line_demand_share` off the record before reasoning about any level.
- **Charter amendment, 2026-09-12 (user): no constant is measured from a WAREHOUSE run** -- no
  clock, no placement, no picks, no travel. Drawing from a declared GENERATOR to characterise its
  own output distribution is not a calibration simulation; it is evaluating a law we wrote down.
  This replaces the flat "there are no calibration simulations" wording with the boundary that
  decision actually meant; the six reference passes stay closed.
- **The era is CALIBRATED again, and the fill-law gap was an artifact** (measured 2026-09-12,
  [Re-take the reference run under v3](issues/46-retake-the-reference-run-under-v3.md)).
  `comparison_20260912_134002` reads **12 arms judged, 0 FAILED** on the equilibrium
  instrument -- fulfillment supply 0.1044 -> **0.0284** against an expected 0.0251 at tol
  0.020, the store 0.0302 -> 0.0271. The v2 sampler's duplicate draws were **95.5-97.1%** of
  the fulfillment gap that 38, 39 and the 40-43 chain existed to explain. The instrument's
  labour clause moved the other way (fulfillment 0.0099 -> 0.0339 against 0.0246 at tol 0.032)
  and is now the closer of the two -- v3 delivers 9.5% more lines to the same crew -- but it
  passes on every arm.
- **The geometry does NOT follow the sampler; two crews do.** `n` is DECLARED
  (`mean_fraction` x section size), so the floors (1.3078 / 1.4994), the levels (3,086,462 /
  2,595,593), the warehouse (2774 aisles / 2,505,050 bins) and the PICKING crew (K=23) came
  back bit-identical across the v2 -> v3 flip. Put-away (60 -> 64) and receiving (22 -> 23) did
  NOT: they size on the replenishment each delivered LINE triggers, and packs/day rose
  15,645.5 -> 17,105.1 (+9.3%), tracking the +9.46% more lines v3 delivers. A crew denominated
  in declared UNITS is sampler-invariant; one denominated in LINES or PACKS is not.
- **The era's declared sampler is v3 since 2026-09-12** ([Fix the sampler's duplicate draws as v3](issues/44-fix-the-sampler-duplicate-draws.md)).
  v2 re-drew SKUs it had already taken and the sku-keyed `Batch.items` collapsed the repeats,
  so every v2 batch delivered fewer lines than the era declared (-8.64% fulfillment on 40/40
  batches, -1.02% store on 29/40). The cause was NOT the float drift the ticket inferred but
  catastrophic cancellation: affinity lift compounds `lift_mult` to ~1e20 against ~1e-6 base
  frequencies, and a Fenwick's subtractive update annihilates the small weights sharing a node
  and keeps the difference as phantom mass (its total read 14.6% above its own leaves). v3 is a
  segment tree that recomputes each node from its children and can never return a dead leaf;
  it delivers exactly `k`, 0/40 short on both channels, at 1.1-1.5x v2. **This is the seventh
  comparability break and the widest** -- it moves every batch sequence, so the coverage fixed
  point, the line floor and the derived picking crew move with it, and the era's CALIBRATED
  status (31) is PROVISIONAL again until
  [Re-run the reference pair and record the form](issues/43-rerun-and-record-the-form.md).
  A collapsed batch now REFUSES under v1/v3; v2 stays exempt so its archive is reproducible.
- **If the floor solve cannot clear the declared confidence inside `_MAX_FLOOR_LINES`** (128 lines
  per SKU, `Optimization/simconfig/coverage.py:84`), **the confidence is declared PER CHANNEL**
  (user, 2026-09-12) -- store and fulfillment each stamping what they can hold, the audit judging
  each against its own -- as an amendment to ADR-0004 rather than a workaround, because the
  finding behind it is that the two channels do not face the same kind of demand. Only fires if
  the solve refuses.
## Decisions so far

<!-- one line per closed ticket: gist + link -->

- [Define the calibrated era](issues/01-define-the-calibrated-era.md): drain-or-cap paced at
  one release per day, cut and rollover on, S = 28,800 s, is the era — and it is the VERIFIER.
  Staffing is a DERIVATION, not a search: per-channel pickers are the one declared input; batch
  content, put crew and receiving crew (site totals) derive from them live at setup, a declared
  scalar at every step (ρ = 0.85, f = 1.0 defaults), seconds-per-unit hybrid (one `fifo`
  reference run; receiving exact). The cost model changes as a HARD BREAK: picking gains a
  per-item charge (0.5 s), put-away a scaled intercept (0.5) and a 0.2 ratio of the charge,
  receiving one charge per PACK (ADR-0001). Six era consequences recorded verbatim.
- [Choose the calibration procedure](issues/02-choose-the-calibration-procedure.md):
  **decisions 1–3 SUPERSEDED 2026-09-06** — no calibration simulations; travel is a closed-form
  expectation computed at setup (13). What stands: the committed record with provenance, `K_max`,
  the exact receiving constant. Was: hybrid under
  `fifo`, as a FIXED POINT (analytic seed, era run, re-derive, ≤2 passes, 5% tolerance); 40 days
  with days 20–39 measured under 04's check as a precondition; one cell, `lpt`, both channels,
  minimal mechanics. `s_pick` per channel as a ratio of sums, `s_put` one site value, `s_recv`
  exact with a failing self-check. Constants live in a COMMITTED calibration record with
  provenance; a catalogue mismatch warns and stamps `calibration_stale`; `K_max` bounds pickers
  with a warn-and-stamp; the measured/analytic travel share may price a new catalogue
  provisionally, stamped `derived`.
- [Design the staffing record](issues/03-design-the-staffing-record.md): pickers are two flat
  global keys on a spliced `STAFFING_KEYS` list; a disagreeing per-arm override RAISES. One
  `staffing` run-spec record with `inputs` (accessor) and `derived` (a pure module,
  `simconfig/staffing.py`, run after precompute) sub-blocks; restore reads the record and
  re-derives (raise on resume, warn-and-stamp on analysis). Legacy crew flags and
  `put_queue_split` under the era are errors. One five-valued provenance enum shared by both
  records (`assumed`/`declared`/`seed`/`measured`/`derived`). The whole record crosses the
  sixth seam onto `sim_result` as one key. PUT_CREW_MODE stays declared; trap fix rides the build.
- [Declare the equilibrium bands](issues/04-declare-the-equilibrium-bands.md): the quantity is
  UTILIZATION (worked ÷ granted), not duty cycle; ρ = 0.85 for all three departments; the band is
  |realized − expected| ≤ `band_tol` (0.10, `assumed`) around a per-department per-leaf
  `expected_utilization` the derivation records — never around ρ, which integer site crews and
  single-channel leaves undercut by construction. The pre-registered check is four STRICT clauses
  over (db, day range): every day drained (from a persisted `shift_days` ledger — today log-only),
  `released_late` = 0 on drained days (nonzero RAISES), utilization in band as a ratio of sums,
  missed share not trending (half-window means within ±0.02). One pure function, two callers: a
  PRECONDITION for the reference run, a REPORT on every other run (below-band picking is the arm's
  saving; a capped day is "declared throughput not delivered"). The sim never judges itself.
  **AMENDED 2026-09-06** by
  [Narrow the drained clause to labour](issues/18-narrow-the-drained-clause-to-labour.md): "every
  day drained" is a LABOUR judgment -- the supply carry is missed share's, never standing work.
- [Sequence the inbound funnel](issues/05-sequence-the-inbound-funnel.md): HOLD phase 1; the lift
  is [Take the reference run](issues/09-take-the-reference-run.md) resolving, not the map closing. The
  pilot's committed regime (crew 4, 12-hour day) is an ERROR under the era, so the pilot gate becomes a
  one-cell inbound-on VERIFICATION read through 04's report, not a search; the funnel inherits the
  reference window (40 days, 20–39 measured); `restock_selection.json` pins the staffing record and
  phase 2 refuses under a different one. Two tickets graduated onto the inbound map (23, 24), 08 gained
  the docstring flip, `CONTEXT.md` gained **Pilot gate**.

- [Add the per-item charge and break the cost model](issues/06-add-the-per-item-charge.md):
  LANDED. `per_pick` is `M·(intercept + qty·per_item + qty·var)`, default 0.5 in PickConfig, the
  pick-model defaults now ONE literal set in the kernel that PickConfig/WorkloadParams/PutawayCost
  reference. The charge flows through `WorkloadParams` into every scorer, labor_cost, W*, the gain
  evaluator and Workload (the lockstep tests demand it). The put crew is priced from the run's
  pick config for the first time (`PutawayCost.from_pick`, scale 0.5 / ratio 0.2) and receiving
  from the put crew (`UnloadCost.from_putaway`, scale 1.0, per-item charge once per PACK). Three
  scale knobs with all five seams (`crew_cost_spec`, `_shared['crew_cost']`). Pre-charge archives
  are rebuilt WITHOUT the term. 1587 unit tests green; sabotage tests pin the evaluator and yardstick.
- [Build the derivation, the calibration record, and the era wiring](issues/08-build-the-derivation-and-era-wiring.md):
  LANDED. `--shift-drain-or-cap` is the era: it completes one release per day, the cut and the
  roll-over, REFUSES the legacy crew flags, and `simconfig/staffing.py` (pure, two stages) derives
  batch content, the put crew and the receiving crew per pair at setup, recorded under
  `staffing.derived[<pair>]` / `staffing.calibration[<pair>]` and carried in the payload. The
  committed record is the pass-0 SEED expressed as travel shares over the catalogue's analytic
  prediction (1.01 / 1.04 / 1.05), never a number. `shift_days` persists the ledger (final day
  flushed outside the tail; the log-only close-out mis-attributed every first batch -- fixed).
  `put_crew_spec` trap closed; `calibration_reference` is the reference run's spec. Two smoke runs
  end to end; 69 new tests.
- [Build the picker staffing seam](issues/07-build-the-picker-staffing-seam.md): LANDED. Pickers
  per channel are two flat global keys on `STAFFING_KEYS` with `channel_pickers()` /
  `staffing_spec()` read at call time; `--store-pickers` / `--ff-pickers`; one nested `staffing`
  run-spec record (`inputs` + `provenance`) restored at BOTH sites; carried in `_shared['staffing']`
  and checked in the worker; stamped WHOLE onto `sim_result` where `EvalContext` reads its own
  channel's count (the literal-25 fallback -- the store's crew on every fulfillment leaf, unread so far -- is
  dead). A disagreeing per-arm `num_pickers` raises at setup, so the four committed modules dropped
  theirs. `PROVENANCE` lives in the `simconfig/constants.py` leaf. Verified by a 68-arm spawn-pool run
  at 7 / 5 agreeing on every surface down to `picker_events`.
- [Build the equilibrium check and the throughput audit](issues/10-build-the-equilibrium-check-and-audit.md):
  LANDED. `simconfig/equilibrium.py` is the one pure function (four strict clauses, a `Verdict`
  with every reading, `expectations_for` the ONE reader of the staffing record for both callers).
  Two facts from the first era run reshaped it: lag on a batch belongs to the CAPPED day before
  it (behind a drained day, or on day 0, it RAISES), and the receiving self-check is EXACT PER
  PACK (an average was 7x off; float tolerance). Caller one is `simconfig/reference.py` +
  `run_reference.py` (subprocess passes, a discarded window RE-SEEDS the next pass and is never
  `measured`, fixed point at <5% daily demand, `--measure`, `--install`); caller two is the
  `throughput.audit` evaluation (`days_capped` ranked behind the new `shift_days` capability,
  utilization in an inspection table because it has no direction). 31 tests; smoke run rendered
  both leaves and measured travel shares of 1.46 / 5.1 / 1.83 against the seed's 1.01 / 1.04 / 1.05.

- [Take the reference run](issues/09-take-the-reference-run.md): TAKEN 2026-09-06 on the
  `bell_lt0` pair — and the procedure as decided produced NO measured record: two passes, both
  windows discarded (0/20 days drained on both leaves), fixed point cut off. Receiving self-check
  exact everywhere. Three causes, all measured: the pick constant converges by ~0.7 per pass so
  two passes stop 10–19% short (store 74→86→95 s/unit, fulfillment 3.1→21.5→25.6; a
  continuation closed it at the FIFTH pass, store ~107 / fulfillment ~29.2 s/unit, pick
  utilization then dead on expectation); the
  catalogue's stock coverage (10 GENERATION batches ≈ 35/47 era days) puts the first reorder
  wave at or past day 40, so put/receiving ran at 30%/2% of pick flow and their bands cannot
  pass in this window — and the same lag defeats "every day drained" through STOCKOUTS (89% of
  fulfillment carry is `unpicked_unstocked` once picking is in band); realized demand runs
  ~3–6% above the derived target (line fraction vs units). Two Windows traps fixed in the
  driver. The user's response the same day retired calibration simulations altogether: the
  successor is
  [Derive the expected-travel closed form](issues/13-derive-the-expected-travel-closed-form.md)
  (the tickets 09 first graduated, 11 and 12, closed out of scope); the inbound map's gates
  23/24 now wait on 13.

- [Derive the expected-travel closed form](issues/13-derive-the-expected-travel-closed-form.md):
  LANDED. `simconfig/expected_travel.py` prices a day of picking and put-away in closed form
  over the catalogue, the built geometry and a placement distribution (the seam: `initial` /
  `uniform`), and `solve_n` is the fixed point; reproduces the converged reference pass to -3.1%
  (store) / -6.8% (fulfillment), put-away exactly. Four decisions: the pair's demand derives from
  the class-uniform expectation (one shared script) with each arm's initial-placement expectation
  stamped for the audit's band; coverage is a runtime rescaling (graduated to 14); `k_max` retired;
  no correction factor -- the bands absorb the residual. The calibration record, the reference
  driver and their spec/flags are gone; the era launches with no record at all.
- [Rescale stock coverage at setup](issues/14-rescale-coverage-at-setup.md): LANDED.
  `simconfig/coverage.py` (the generator's formula in days, the floor shares) and
  `simdriver/era_coverage.py` (stage A factored out of the derivation; the pair-level fixed
  point `Q(n) -> plan -> geometry -> n`, a bracketed log-space secant because the map's gain
  is above one) drive from `build_shared_assets` under the era; `coverage_days` (10) and
  `safety_days` (2) ride `STAFFING_KEYS`; the loop's record lands under
  `calibration[<pair>].coverage`; the placement fingerprint rides every arm stamp. Flag-off
  the planner runs once, proven. THE FINDING: the catalogue's levels were worth 1,771 store
  days, so at 10 days the store collapses to 722 aisles, 71% of its SKUs hold one unit and
  93% hold less than one line -- the 40-day run reorders from day 1, drains 0 of 40 days and
  picks 3.8% of demand. A wave inside the window and a store that is a warehouse are mutually
  exclusive under the unit floor; the default is provisional and the floor is ticket 15.

- [Choose the coverage floor](issues/15-choose-the-coverage-floor.md): the floor is ONE
  LINE of the SKU's own mean line, on Q and the reorder point, so a floored SKU runs base-stock
  (`rp = Q - 1`, every pick reorders what it took); `floor_lines` (1.0, `assumed`) joins
  `STAFFING_KEYS`; 10 / 2 stay and are inert on this catalogue (both sections 100% on the floor,
  the record says so). The record stamps the expected first-pass fill rate; the lead pipeline is
  stamped per SKU, not inferred from `rp`; the drained clause becomes labour-only (amends 04);
  "a wave inside the window" is retired -- base-stock is a trickle from day `lead`, and the
  store's answer is explicitly no wave. USER AMENDMENT: the line distribution is stamped on the
  SKU (two inventory columns through the schema pipeline; pre-stamp vintages reconstruct at
  load) and every reader -- sampler and closed forms alike -- reads the one object; the grilling
  found `staffing.py` and `coverage.py` already carried two different line means. Three task
  tickets graduated (16 -> 17, and 18).

- [Narrow the drained clause to labour](issues/18-narrow-the-drained-clause-to-labour.md):
  LANDED. `equilibrium.is_drained` is the ONE definition (nothing cut, no put/dock standing, no
  `unpicked_daycut` carry; the supply carry is not an argument); the runner writes the ledger's
  `drained` with it and the clause reads the column, never re-derives. `shift_days` carries the
  carry split (`standing_carry_labour` / `standing_carry_supply`; sim_db 487a65bf83a9 ->
  798778f4fae1, the old vintage served NULL through a `shift_day_frame` override), the clause
  REPORTS the days that drained with supply carry standing, `days_capped` follows. A 6-day era
  smoke run: the fulfillment leaf drained days 2-5 with 4-11 units of supply carry standing,
  every one CAPPED under the old rule. Amends 04; *Standing work* amended in the glossary.
  **AMENDED 2026-09-07** by
  [Overtime behind a drained day raises the instrument](issues/21-overtime-behind-a-drained-day.md):
  overtime is labour that did not fit the day -- `is_drained`'s fifth term.

- [Stamp the line distribution on the SKU](issues/16-stamp-the-line-distribution-on-the-sku.md):
  LANDED. `Demand.line` (`LineDistribution`: `mean`/`cdf`/`quantile`/`survival`/`expected_min`/
  `sample`; one family `poisson_max1`, Knuth's draws) is read by the batch sampler,
  `expected_travel`, `staffing` and `coverage`; two `cartons` columns ride the schema pipeline
  (`inventory_db` 0e234fbfc739 -> 4ff06991df47, the pre-stamp vintage vetted with a `dataset`
  override, its reconstruction `assumed` and named in `calibration[<pair>].line_law`); goldens
  captured pre-stamp prove draws, batches and both cache fingerprints byte-identical.  Two
  corrections: the ticket's `Order.py` site is the WEIGHT law (the sampler lives in
  `Workload_Builder`), and the `max(1, lam)` drift lived only in the recorded `analytic` block --
  era batch content already read the exact mean, so nothing about it moves.  17 is unblocked.

- [Build the line floor](issues/17-build-the-line-floor.md): BUILD LANDED; the 40-day
  check ran and did NOT read in band.  `coverage.line_floor` (`ceil(floor_lines x E[line])`,
  so `L = λ + 1` on every Poisson SKU) floors Q and rp, a floored SKU runs base stock,
  `pipeline_qty` is stamped on the SKU (an `Order` slot and a `cartons` column, `inventory_db`
  4ff06991df47 -> 025f4b1548a9; `Order.pipeline_allowance` the one reader, cleared flag-off at
  both load sites with an EXPLICIT era flag because a worker's CONFIG is pristine), the record
  carries `final[<ch>].fill` priced on the PLANNED levels, `floor_lines` rides the seams.  Two
  derivation corrections: the base-stock lot is the mean line (was 1: a tenfold receiving
  over-count) and the pick load is SERVED units x s_pick (= ρ; demanded units read 0.98).
  THE CHECK (`comparison_20260906_222118`, fifo, 40 days): fixed point in 2 rounds, both
  sections 100% on the floor, no wave confirmed -- but fulfillment's planner fielded 30% of
  SKUs BELOW their floor (a treadmill: missed share 0.663 rising, 88,857 units of supply carry,
  39/40 capped), the store fielded its floor and fragmented (put-away never tops up an
  occupied bin: 215k of 240k SKUs on >1 bin by day 40, missed share 0.127 -> 0.283 vs 0.095),
  and the store audit RAISED on overtime behind a labour-drained day.  Graduated 19, 20, 21;
  every era number stays PROVISIONAL until they resolve and the check is re-read.

- [Field the floor](issues/19-field-the-floor.md): the floor is a PROMISE and the catalogue
  carries NO stock levels. The check's shortfall was the tier MIX, not the section's size:
  fulfillment's fixed 0.5/0.3/0.2 split against a required 11/63/26 drained ff_medium, and
  demand mode alone leaves 15.8% short because `_add_one` charges the emptiest bucket by
  absolute count, not the requirement's tier (the store's 7.9% too). So: the planner sizes
  every bucket from `bucket_requirements` and fields each SKU at exactly that packing (no
  growth, no shrink), checked against EMITTED capacity, refusing when a cap binds; the fixed
  tier distribution is retired (depth classes / aisle split survive); the record stamps a
  `fielded` block. The user's reframing removed `equilibrium_qty` / `reorder_point` /
  `stock_plan` from the catalogue as a schema vintage, the generator's coverage-in-batches
  knobs with them; the fixed point runs in every mode on the reporting frame's day -- a hard
  break for flag-off inventories (ADR-0002). Graduated 22 -> 23.

- [Overtime behind a drained day raises the instrument](issues/21-overtime-behind-a-drained-day.md):
  LANDED. `is_drained` gains `overtime` (`last_finish > cap_end`) as a fifth required term --
  labour that did not fit the day caps it -- and the runner passes the stamp. The amendment
  moves no column (sim_db stays 798778f4fae1), so the `shift_day_frame` query folds the term
  into `drained` off the row's own two stamps for every vintage (the ONE stated exception to
  18's read-the-column rule; a no-op on an amended ledger), and the check, the audit frame and
  `days_capped` follow. The store leaf of 17's check re-reads without raising: 36/40 capped
  (35 + day 3), day 4's 138 s recorded behind it; fulfillment unchanged at 39/40.

- [Let a base-stock top-up reach the shelf](issues/20-let-a-base-stock-top-up-reach-the-shelf.md):
  DECIDED (user, overruling the home-bin recommendation): put-away fills an EMPTY bin first and
  consolidates into the SKU's own bin only when none fits -- the new-bin decision is where
  optimisation happens (ADR-0003). Premise corrected: picks already sum across bins; the real
  failure was the free index running dry against one-bin-per-SKU sizing, units pending forever.
  Chain: empty -> own bin (fullest first) -> rescues -> pending; picks drain the smallest bin
  first. Inbound does the repacking: rescues are priced to the receiving crew per resulting pack
  and recorded (a `sim_db` vintage: `repack` work events, bin state on `bin_placement`, three
  flows + free-bin depth on `batch_stats`); expected repacks stamped zero and flagged. No knob --
  a no-op wherever the index never exhausts. Graduated -> 24.

- [Retire the authored stock levels](issues/22-retire-the-authored-stock-levels.md): LANDED. The
  catalogue carries no level; a level is a run's DECLARATION, made in every mode and recorded by
  the run that made it. The four columns (`pipeline_qty` too -- same kind of fact, and leaving it
  would keep the always-NULL column decision 7 rejected) left `cartons` for a new `stock_levels`
  table a generated catalogue leaves EMPTY; `inventory_db` 025f4b1548a9 -> 4536857cb860, the
  outgoing vintage still served by an override reading the four out of ITS `cartons`.
  `Order.declare_stock` is the one mutation site. The dangerous half was the DEFAULT, not the drop:
  the bin count is demand-derived from the levels on EVERY path (`sample=False` skips only the SKU
  sampling), so `_equilibrium_qty`'s fallback of 1 would have built a warehouse an order of
  magnitude too small in silence -- it now RAISES (memory `warehouse-size-comes-from-the-levels`).
  Three consequences the ticket did not foresee: a REBUILD must re-declare from the run's own
  recorded `lines_per_day` (`declare_from_record`, one pass, not a fresh fixed point); so the
  declaration must be RECORDED in every mode including at the multi-cell freeze (preflight's 2-cell
  canary caught this -- a full run tree and an empty analysis one); and round 0 stopped planning,
  becoming an ANALYTIC SEED (`seed_lines`), killing the record's `catalogue` block and
  `implied_coverage`. Flag-off and era-on derive IDENTICAL levels and the identical warehouse
  (measured). All nine gates green.

- [Field the requirement](issues/23-field-the-requirement.md): LANDED. The planner has ONE
  contract -- `_packing` answers sizing and fielding in a single pass, so every bucket is sized
  from what the declared levels need and every SKU is fielded at exactly its declaration,
  packed the way that statement counted it. `sample_to_capacity` is gone (no re-choice, no
  growth, no shrink, no `rng` in planning); fulfillment's fixed tier distribution is retired
  and RAISES rather than being ignored; the promise is checked on EMITTED capacity per bucket
  and refuses with `UnfieldableRequirement`, which carries the shortfall structurally.
  `coverage.final[<ch>].fielded` stamps the proof and `fixed_point` raises on it (the key is
  `above_declaration_skus`, not the ticket's "above floor" -- the two coincided only because
  that pair sat 100% ON the floor). THE CHECK: re-planning the check run's pair at its own
  recorded line count gives **0 SKUs below floor and 0 grown on BOTH sections** (was 30.3% and
  64,989), sum Q equal to the declaration, and fulfillment at **1,232 aisles against the run's
  977** -- 19's predicted number. Three defects found: `Singleton`/`FulfillmentBin` SUBCLASS
  `Pallet` (so the packing flag must be tested positively), and BOTH aisle splits rounded to
  nearest and sacrificed more than the declared loss (`_ff_depth_split` refused 30 of ~90
  depth-class configs). Two consequences: a bin cap is now SELF-DEFEATING under the fixed point
  (a smaller warehouse raises the derived lines/day and so the levels -- `--coverage-days` is
  the lever, and the preflight canary moved onto it), and the aisle split now trades aisles for
  travel rather than capacity for travel, so archived `ks` sweeps are not comparable.

- [Build the empty-first top-up](issues/24-build-the-empty-first-top-up.md): LANDED. Put-away
  fills an empty bin first and consolidates into the SKU's own bin only when no empty bin fits
  (ADR-0003); the repack/singleton rescues became RECEIVING work, priced per resulting pack at
  the dock's unload price. One `sim_db` vintage (`798778f4fae1` -> `02a78953886c`):
  `bin_placement.bin_state`, and `batch_stats` gains `put_topups` / `recv_repacks` /
  `recv_repacked_packs` / `free_bins`. A fifth equilibrium clause, `rework`, JUDGES the repack
  against the record's `f_repack = 0` (provenance `assumed`) and REPORTS own-bin share and
  free-index depth without judging them. THE ONE THING THAT DID NOT COMPOSE: decision 7's
  smallest-first drain and the ticket's byte-identity gate are incompatible -- measured, 800-1,145
  of ~1,200 SKU-tiers hold 2+ bins straight out of initial stocking and 224-327 are
  drain-order-sensitive, so the drain change moves travel on essentially every run and is gated on
  nothing. The user chose to land both and accept the break, which also retires the forward-pick
  preference from the drain. **Absolute pick/travel/throughput numbers do not cross this commit**
  (memory `drain-order-is-smallest-first`); it is the third such break in a fortnight, after the
  per-item charge and 23's aisle-split inflation. 1,823 unit / 394 integration / 31 e2e green, 27
  of 27 mutations caught, both preflight canaries end to end.

- [Re-read the check under the fielded floor](issues/25-re-read-the-check.md): TAKEN 2026-09-08
  (`comparison_20260908_075736`, fifo, 40 era days, the reference pair, 16 min). 17's two
  structural defects are GONE and the numbers still do not read in band -- but the failures are now
  two sharp questions instead of two broken mechanisms. FIXED and confirmed live: both sections
  field EXACTLY (0 SKUs below floor, 0 above declaration, sum Q equal to the declaration) in a
  2,341-aisle warehouse, the +255 aisles 23 predicted; the fulfillment treadmill is gone (missed
  share 0.663 RISING -> 0.118 with trend -0.010, the clause PASSING; drained 1/40 -> 18/40; supply
  carry 88,857 -> max 5,412); both fill rates are now 0.922 because both sections sit on the floor
  at their declaration; the store audit RENDERS (21) with `overtime_only_days` 0; `released_late`
  is ok on both, all lag behind CAPPED days; receiving is in band on both. **`rework` reads clean
  on its first real run**: 0 packs repacked over 0 rescues, own-bin share exactly 0.000 every day,
  free index never below 1,197,833 of 2,096,050 bins -- ADR-0003's `f_repack = 0` confirmed, and
  17's store cause (215k SKUs on 2+ bins) never engaged. STILL FAILING, two things: (1) put
  utilization is out of band on BOTH leaves in OPPOSITE directions (ff 0.477/0.691, store
  0.362/0.155) because realized put-away costs **98.50 s/unit on the store against 29.15 on
  fulfillment** while the record prices both at ONE site-wide `s_put = 41.24` -- units are right
  within 2% and the SITE total within 0.9%, so the crew of 59 is correctly SIZED and wrongly
  APPORTIONED, and put is the only department of the three that multiplies per-channel units by a
  site constant (`s_pick` is per channel, receiving takes per-channel seconds from the script);
  (2) the store never reaches a steady state -- 0/20 days drained in the window, picking 0.963
  against 0.850, missed share rising for ~30 days then falling, all at a FLAT 100.4 -> 99.2 s/unit,
  so it is neither fragmentation nor travel drift. **The era's numbers on both sections stay
  PROVISIONAL** and 05's hold on the inbound funnel stands. Graduated 26 and 27.

- [Give put-away a per-channel expected travel](issues/26-give-put-away-a-per-channel-price.md):
  LANDED 2026-09-08. The defect was ONE line: `ScriptTotals.put_s` was already the channel's own
  expectation and the record already stamped it, but `workunits` collapsed both channels into one
  ratio and `derive` re-expanded it as `units x that`. `constants['s_put']` is now a
  `{channel: constant}` map like `s_pick`'s, and `put.s_put` in the record is a channel map with
  NO site scalar beside it -- there is nowhere left to price a put from a site-wide number.
  THE CHECK (`comparison_20260908_094846`, same spec): **`utilization` reads OK on BOTH leaves for
  the first time** -- fulfillment 0.477 realized against 0.475 expected (was 0.691), store 0.362
  against 0.371 (was 0.155) -- with put crew 59 and site load 1,437,958 s/day IDENTICAL to the
  pre-change run and every other clause reading byte-identical, so the simulation did not move,
  only the expectation. Both leaves still fail on `drained` and `missed_share`, which are 27's
  question; the department bands are no longer among the reasons. The ticket's proposed key split
  (`--s-put-store` / `--s-put-ff`) was REJECTED: the flag has no caller anywhere and the key is
  published into `held_fixed.json`, so the rename crosses the experiment boundary for zero
  numerical gain -- it stays one key that stamps a declaration onto every channel, and the
  symmetry rename is available later with no numerical content. Three corrections to the ticket's
  own premises, all from adversarial verification: the crew invariance is NOT an identity of
  `derive` (it needs equal batch counts, now RAISED on, and it is 1 ulp inexact, so the test pins
  the two LOADS agreeing rather than `crew == 59`); the closed form is NOT already correct (-2.8%
  on fulfillment, and the store's +0.4% is travel -6.3% cancelling handling +1.8%) -> graduated 28;
  and the per-channel price is per-channel only BY SIDE EFFECT of the two sections' BinKeys being
  disjoint, now pinned as a provable invariant. Also found: the put band has no per-arm
  re-centring (-> the ranked-arms fog patch), and `s_recv` is a dead field whose comment claimed a
  reader it never had.

- [Fit the store's window to its own steady state](issues/27-fit-the-store-window-to-its-steady-state.md):
  RESOLVED 2026-09-08 -- **the window was the wrong lever**; 40 days / 20-39 measured, one site
  window, stands. The store's series showed NOTHING IS LOST under the era (missed and cut units are
  re-offered next day, lead-0 top-ups land first; picked + standing = script demand exactly), so
  `derive` had sized both crews on SERVED units (demand x 0.922) when they must pick all of demand
  -- 0.92 on the record's own numbers, and fulfillment passed only on a script 6.7% light. The
  missed-share hump was pure labour overflow (`unpicked_daycut`) from a queue near saturation;
  the supply share was flat at 0.070 vs 0.078 expected, on-hand flat, every queue zero; and the
  clause was READING day-cut + stockout with re-attempts double-counted while calling itself
  supply-only. The day law is a DECLARED Gaussian on the line count (cv 1/3 store, 1/4 ff), so
  14/40 days exceed a full day at any headroom and "every day drained" is not a property of
  equilibrium. Ten decisions: crews size on DEMANDED units; the actual script is priced; the
  declared input FLIPS (demand declared in the sampler's unit, crew derived, picker flags refused
  under the era -- ADR-0004); `rho_pick` is replaced by one joint **first-time confidence**
  (0.95: reached on its day AND filled), per pick, split equally, the floor solved for fill >=
  sqrt(c) (~1.27 lines, +19% stock) and the smallest integer crew for expected cut share <=
  1 - sqrt(c) (store ~32 at ~0.72) -- closed forms over the chosen inventory; put/receiving keep
  rho; `missed_share` splits into `supply` and `labour` clauses and strict `drained` becomes a
  reading; both leaves re-checked before the hold lifts. Graduated 29, 30, 31.

- [Close the put closed form's three known gaps](issues/28-close-the-put-closed-forms-gaps.md):
  LANDED 2026-09-08. The put formula was right; it was priced over ONE rounded lot. `fired_lots`
  now walks each SKU's script lines against its declared level (base stock: `q // Q` lots of Q and
  one of `q % Q` -- 1.3 lots per line on the reference pair; above the floor the position rule,
  fractional) and `received_law` hands the packer the ledger's jitter as a distribution, each
  (SKU, arrived quantity) packed once by the sim's own packer. Re-priced offline against the check
  run's `work_events`: units per pack +6.99% / +3.05% -> -0.38% / +0.12%; on the realized unit
  mix put reads +0.21% / -0.16% and receiving +0.01% / -0.09% (the store's script-mix +1.8% is
  the 9,763 heavier units still standing at day 40, uniform across every term including
  receiving). Put crew 59 -> 61, receiving 22 unchanged; against crew 59 the fulfillment band
  re-centres at the predicted 0.489 (realized 0.477) -- a fidelity fix, every band inside
  `band_tol`. A nonzero `--put-swap-coef` and a split queue are REFUSED under the era at the
  parser and at the seam (the single queue never read the coefficient anyway); an unbuilt
  BinKey raises `UnbuiltClass` on every reader instead of pricing at zero.

- [Declare the demand and derive the crew from the joint first-time confidence](issues/29-declare-demand-derive-crew.md):
  LANDED 2026-09-08 (ADR-0004 built and amended). Three new staffing keys on all five seams --
  `store_demand` / `ff_demand` (the sampler's unit; defaults the previous fixed point, so the
  reference warehouse does not move) and `first_time_confidence` (0.95) -- and THE REGIME
  DECIDES WHICH KEYS ARE INPUTS: the era records the picker keys and `rho_pick` as None /
  `derived`, flag-off records the demand and confidence as None, and `_check_era_flags` refuses
  the other regime's flags both ways. The crew side is a Normal partial expectation on the
  day's UNITS (`staffing.solve_pickers`, unit cv from decision 8's two-term variance, MC-checked
  to 5%): on the reference store's own numbers **K = 32 at 0.720, cut share 0.0203**, where the
  served-unit derivation fields 29 at a real 0.042 (the sabotage test). The shelf side
  (`coverage.solve_floor_lines`) bisects the fill's step function PER SECTION at the declared
  line count, before any level is declared; the fixed point collapses to ONE round under the
  era. Two decisions: a typed `--floor-lines` is accepted at or above the solved value and
  REFUSED below it; the guarantee prices `expected_pick`'s seconds per unit at the declared day
  rather than the Gauss-Hermite sum. `derive` prices the sampled script's DEMANDED units, its
  utilization is derived, `rho_pick` is unread; `staffing.channel_crew` is the ONE crew reader
  (worker check, expectations, EvalContext). Verified: 1,832 + 32 unit, 394 integration (one
  PRE-EXISTING failure, the `throughput.audit` figure attribution), 4 e2e, both preflight
  canaries, and an era canary through the pool: floors solved 1.2728 / 1.2858 at fill 0.975,
  the DERIVED crew in every arm's run params. The reference pair's own floor and crew come with
  31's run; the era's numbers stay PROVISIONAL until it reads clean.
- [Split the missed-share clause into supply and labour](issues/30-split-the-missed-share-clause.md):
  LANDED 2026-09-09 (AFK build). `missed_share` and the strict `drained` are gone; `supply` and
  `labour` judge the two causes apart, both as `carryover` FLOWS over FRESH demand
  (`equilibrium.demand_flows`: fresh = the effective batch less the previous batch's whole carry,
  a stockout counted on its first attempt per SKU, the cut on every attempt). `supply`: level
  within a typed 0.02 of the stamped `1 - fill`, trend as before. `labour`: cut share within 2
  sampling sds of the stamped expected cut share (the sd is a new closed form,
  `staffing.cut_share_sd`, so the band is the declared law's over the window), the day-end
  labour carry under one day's capacity in units, the per-day cut share not trending (its band
  derived the same way -- the review found a typed 0.02 failed 26-52% of healthy windows; the
  supply trend keeps the typed number); a capped day is a READING, a missing day still fails.
  The first-attempt supply count is a LOWER bound (per-SKU fresh demand is unrecorded). The audit's table carries both shares per arm;
  `Diagnostics/equilibrium_report.py` prints the verdict per leaf off a finished run. Re-read on
  the 2026-09-08 pair, days 20-39: store supply 0.080 vs 0.078 PASSES, labour FAILS on the
  overflow's trend (0.311 -> 0.130); fulfillment passes every clause. The ticket's "0.070" was
  the raw flow over effective demand; raw over fresh would be 0.092 and fail -- the re-attempt
  rule is the definition. Neither leaf's cut-share LEVEL is judged until 31's run stamps a
  guarantee.
- [Re-check the reference pair under the first-time guarantee](issues/31-recheck-under-the-first-time-guarantee.md):
  RESOLVED 2026-09-09. One 40-day era run on the reference pair (`comparison_20260909_204522`)
  under 29's derivation and 30's clauses: **both leaves pass every clause on days 20-39**, so
  the era's numbers stop being provisional and 05's hold on the inbound funnel LIFTS (the
  inbound map's 23 / 24 may proceed). Stamped: store K = 31 (cut share 0.0227 vs 0.0212, band
  ±0.034; carry max 0.08 day; supply 0.026 vs 0.025), fulfillment K = 23 (0.0130 vs 0.0254;
  0.027 vs 0.025); floors 1.2728 / 1.2668 lines at fill 0.975; the warehouse 2,466,650 bins over
  2,761 aisles (+17.7%) holding 5.24 M units (+20.3%). The sampled script's day cv runs under
  the declared law on both sections (0.28 / 0.21 vs 0.33 / 0.25, ~1.5 sd of a 40-day estimate).
  The `receive` rows 27 could not explain are the era's DERIVED receiving crew (22): the run
  spec's `recv_crew_size 0` is the flag-off key the era never reads, and the rows are priced by
  the receiving utilization clause. Own-bin share 0.000 and ~58% free again -- graduated.

- [Band the own-bin share and the free-index depth](issues/32-band-the-own-bin-share-and-free-index.md):
  RESOLVED 2026-09-10. The ticket's 57-59% was an ARTEFACT: `free_bins` counts the whole geometry,
  so each leaf reads the other section as free; against the record's per-bucket `fielded` block
  the real headroom is 18.0% / 15.2% (the 0.85 fill), and it FALLS every day of the run (store
  -6,763, fulfillment -18,279) because base stock + empty-first + smallest-first drain settles a
  picked SKU at two bins -- ADR-0003's consequence 1 is the prediction of this, and the window
  cannot see where the store's slide ends. Decided: own-bin share and a newly recorded TIER SPILL
  are judged at exactly zero with no knob; the depth is recorded PER BUCKET (the leaf's own section
  by construction) and REPORTED against its stamped setup free; the fill headroom becomes DERIVED
  from the stationary fragmentation closed form (a Markov chain over the line law); `free_bins`
  keeps its name and meaning. Graduated 33, 34, 35; three glossary terms; ADR-0003 observation.

- [Build the per-bucket free index, the tier spill and the judged rework clause](issues/33-build-the-per-bucket-free-index-and-judged-rework.md):
  LANDED 2026-09-10 (`sim_db` `02a78953886c` -> `b87cfbb8d041`). The tier spill is counted at
  the placement commit point (`put_spills`, the tier pair on `bin_placement`), the free index is
  written PER BUCKET (`free_index`, one level per BinKey per batch), and `rework` judges spills,
  top-ups and repacks at zero -- no knob -- naming the dry bucket, while the depth is reported
  per bucket against the record's `setup_free`. THE CHECK on the reference pair: batch 0
  reproduces the record's setup free on all 60 + 3 buckets exactly, 0 spills / 0 top-ups, and
  batches 0-1 of every existing surface are IDENTICAL to the 2026-09-09 run. Found on the way:
  the "optional-fill serves older vintages" claim was never true -- a pure column addition needs
  a per-vintage override or the loader falls to its legacy dataclass 0 (`free_bins` read 0 on
  798778f4fae1 files for two days; fixed, memory
  `optional-fill-only-answers-through-an-override`).

- [Derive the stationary fragmentation closed form](issues/34-derive-the-stationary-fragmentation-closed-form.md):
  LANDED 2026-09-10. Each SKU is a Markov chain over the multiset of units on its shelf (a unit
  keeps the size tier it was packed at), driven by its line law: smallest-first drain, the
  position rule's lot packed by the plan's leading slots into fresh bins, a shelf-emptying line
  refilling the fielded state. Solved per (regime, law, plan, rp) class -- 489 classes for the
  pair, 16 s -- and stamped in EVERY mode: `fielded.buckets[].expected_extra` and a
  `fielded.fragmentation` block (`derived`). Checked on both finished runs: at each SKU's realized
  line count the chain reproduces both leaves to -3% (tolerance +/-5% per SKU; jitter is the
  named residual); the store's 39-day drawdown to -4% and every keyframe tier within 10%. The
  fulfillment transient over-reads at the declared line share because the sampler's affinity
  lift touches a quarter fewer SKUs than the share says -- a seam takes a per-SKU rate. The
  pair: store +62,305 bins (28% of its headroom), fulfillment +48,204 (26%); six store `small`
  buckets exceed their setup free two to three times because picked singleton remainders come
  back as pallets of 1 -- what 35 sizes.

- [Derive the fill headroom from the fragmentation](issues/35-derive-the-fill-headroom-from-the-fragmentation.md):
  LANDED 2026-09-10. Under the era each bucket is sized to HOLD `max(requirement + E[extra],
  requirement / (1 - min_headroom))` bins -- the declaration AND the fragmentation base stock
  creates -- through a planner `bucket_hold` map derived each round before the plan
  (`era_coverage.derive_fill` / `derived_holds`), recorded per bucket (`fill`, `hold`,
  `headroom_floored`, the `fielded.fill` block) and read back by every rebuild (`holds_at`);
  `min_headroom` (0.05, `assumed`) is a new era-only staffing key on all five seams, the typed
  fills refuse under the era and record as None. Flag-off is byte-identical (per-bucket oracle).
  THE REFERENCE PAIR: 2,761 aisles / 2,466,650 bins -> **2,536 / 2,311,000**; the store's `small`
  buckets grow (food/small 76,000 -> 98,000, fill 0.652), every `singleton` bucket shrinks to the
  floor; crews and floors unchanged. The fourth comparability break (memory
  `derived-fill-is-the-fourth-comparability-break`). Found on the way: a multi-cell era run lost its
  coverage record to the first cell's derived block and its analysis rebuilt nothing -- fixed.
- [Declare the coverage against the inbound lead](issues/36-declare-the-coverage-against-the-inbound-lead.md):
  **the lead is a SKU attribute (supplier lead) plus the trailer's transit, and the record
  derives it.** Order-to-shelf under the pipeline is the lognormal transit rounded UP to the
  day grid: `E[ceil(L/D)] = 1 + sum_k (1 - Phi(ln(kD/m)/sigma))` reads 1.766 site days at the
  pilot regime against the run's 1.78 / 1.75 -- transit and grid only, the yard's excess is the
  campaign's effect. Per SKU `lead_days_s = attr_s / releases_per_day + transit_days` (the
  batches-as-days bug at `coverage.py:171` ends); the fill closed form takes the lead and the
  floor solve absorbs it, so the first-time promise holds AT the lead (more fulfillment stock;
  a new era for inbound-on numbers); the line share stays the per-SKU rate until 26's residual
  says otherwise; `safety_days` unchanged; a catalogue lead the pipeline would discard refuses.
  The audit reports the realized Little lead beside the stamp and `1 - fill(realized)` as the
  explained level. A site-level lead law on the era was rejected (user); phase 1 runs with the
  yard on so the funnel shares one record (comment on inbound 24). Graduated:
  [Build the lead-aware coverage record](issues/37-build-the-lead-aware-coverage-record.md)
  (here) and inbound 27 (the dispatch chaining), both blocking inbound 26.
- [Build the lead-aware coverage record](issues/37-build-the-lead-aware-coverage-record.md):
  BUILT 2026-09-10 -- `coverage.transit_day_law` stamps the pair's day-grid transit
  (`coverage.lead`, 1.7656 at the pilot), every level, pipeline and fill is priced at the
  SKU's supplier lead plus it, the floor is solved there, the audit reports the realized
  lead (Little's law) beside the stamp with the level it explains; a discarded supplier
  lead refuses until inbound 27. Reference pair: fulfillment floor 1.2668 -> 1.4994, warehouse
  2,536 -> 2,774 aisles; inbound-off byte-identical (the fifth comparability break).
- [Measure the repeat structure and the realized lead distribution](issues/39-measure-the-repeat-structure-and-lead-distribution.md):
  neither assumption carries the residual. Temporal dependence is structurally impossible
  (each batch is seeded on its own; no cross-batch state) and measures NEGATIVE where the gap
  is (-0.0089 fulfillment). The lead's realized pmf, reconstructed exactly from the trailer
  draw, is worth +0.0007 (0.9%), and the parent's Little lead was never partial -- the
  receiving and put-away queues add <= 0.031 d. What IS the mechanism: the record under-prices
  its own event -- a prior line for the same SKU inside the lead -- by **3.74x** on
  fulfillment (0.0399 share-law-true control vs 0.1494 realized). One multiplier on the
  declared rate, fitted to that probability ALONE, then recovers **71%** of fulfillment's gap
  and **52%** of the store's, both moving the same way. Asset:
  [measure_repeat_and_lead.py](assets/measure_repeat_and_lead.py).
- [Close the fulfillment fill-law gap](issues/38-close-the-fulfillment-fill-law-gap.md): the line
  share `pi_s = freq / sum freq` is the WRONG MARGINAL. The sampler draws `k` DISTINCT SKUs per
  batch without replacement, multiplying each survivor by `prod lift(A, B)` over the partners
  already drawn, so a weight share is not an inclusion probability. The floor solves against the
  **draw probability** `p_s` instead -- DEFINED as what the declared sampler does, characterised by
  a generator-only draw of M batches on the run's own `seed_batches` (no warehouse, no simulation),
  with `N ~ Binomial(K, p_s)` riding inside the same change because `p_s` is a probability, not a
  rate. The structure predicts 39's fitted `m` without being told it: expected drawn cluster-mates
  per candidate is `cluster_size * k/N` = **0.20 store against 1.45 fulfillment** (7.4x), while
  `relative_frequency` dispersion is close and tilted the WRONG WAY (measured on the reference
  catalogue, which is the BELL profile: fulfillment CV 0.5813 against the store's 0.6389) -- the
  MORE dispersed section is the less concentrated one, so a frequency story predicts the opposite
  ordering. One mechanism at two sampling densities, which is why every per-SKU RATE variant failed. Affordable
  because there is NO fixed-point circularity: under the era the two `units_per_line` cancel, so
  `mean_fraction == STORE_DEMAND / FF_DEMAND` and `k` does not depend on `n`. One rate everywhere
  (`daily_demand`, `expected_travel`, `staffing`, the fragmentation transient) in ONE commit,
  era-only with flag-off byte-identical, cached as a fingerprinted pair-directory artifact with a
  derivation identity that makes a rebuild REFUSE. Gated generator-side BEFORE any warehouse is
  built -- the record's line-weighted prior-line probability within 10% relative of 39's empirical
  on both channels -- with "fulfillment closes, store overshoots" fatal by rule and "short on both"
  a rejection, not a stamped residual. Whatever floor the solve then asks for is the warehouse
  bought; 38 decision 4 (confidence per channel) survives only as the fallback if
  `_MAX_FLOOR_LINES` refuses. **The mechanism is argued from the sampler's structure, NOT yet
  measured** -- the gate exists to falsify it in minutes. Four `task` tickets carry the build.

  **INVALIDATED ON ITS CENTRAL CLAIM, 2026-09-12** by
  [Re-measure the fill-law targets under v3](issues/45-remeasure-the-fill-targets-under-v3.md):
  the concentration this rests on was the v2 sampler's duplicate draws, not affinity. The
  ticket stays closed -- the route did walk through it, and its rejected alternatives (the
  per-SKU rate substitution, the re-weighted lift) are still rejected for their own reasons --
  but do not carry its numbers forward.
- [Fix the sampler's duplicate draws as v3](issues/44-fix-the-sampler-duplicate-draws.md):
  BUILT and declared. The inherited mechanism is REFUTED (0/73 duplicates in batch 2 had
  `u > true_total`; drift alone produces none at all) -- it is catastrophic cancellation under a
  ~1e26 weight range, and the `2^k - 1` index signature is its consequence, not its cause.
  `_SegTree` recomputes every node from its children, so its root is bit-for-bit a rebuild and
  its descent cannot enter dead ground. v3 delivers exactly `k` (store 618.1/618.1, fulfillment
  2,980.6/2,980.6, 0/40 short) at 1.10x / 1.53x v2 and ~1/35th of v1; v1 and v2 are byte-untouched.
  USER DECISIONS: the era flips (`SAMPLER = 'v3'`), and a collapsed batch refuses under any
  sampler promising distinct draws (v2 exempt). 9 gates in `Tests/unit/test_batch_sampler_v3.py`,
  proven non-vacuous against v2. v2's second failure mode -- an early break returning fewer than
  `k` with no repeats -- is recorded so a short batch is never read as a collapse.

- [Re-measure the fill-law targets under v3](issues/45-remeasure-the-fill-targets-under-v3.md):
  the targets are REVERSED, not restated. v2's defect manufactured the evidence -- 335
  fulfillment SKUs were drawn on at least half the measured days (one on 17 of 20) and carried
  the repeat statistic; under v3 the maximum is 5 and none reaches half. Every symptom is gone:
  touched-SKU shortfall -25.2% -> **+3.3%** (38's concentration premise is DEAD), lag-1
  suppression 0.838x -> 1.014x (GONE, so the Out-of-scope entry below is amended), and the
  prior-line probability 3.74x the record's Poisson -> **0.87x**. New targets **0.00721 store /
  0.03485 fulfillment**: the record now UNDER-prices the store 19% and OVER-prices fulfillment
  16%, in OPPOSITE directions, so 39's single multiplier cannot be the shape. The realized
  missed shares are v2 outcomes and could not be re-measured here. USER DECISION: hoist the run
  in front of the form work -- 40 now waits on
  [Re-take the reference run under v3](issues/46-retake-the-reference-run-under-v3.md).
  The v2 baseline was reproduced first, exactly, so every difference is the sampler.

- [Re-take the reference run under v3 and re-establish the gap](issues/46-retake-the-reference-run-under-v3.md):
  **the gap is CLOSED** -- 12 arms, 0 failed, fulfillment supply +0.0793 over expected -> +0.0033
  in a 0.020 band. The realized lead was re-measured (drawn K 1.6742, realized 1.78-1.84, 310
  trailers) and re-pricing at it moves fulfillment +0.0002. This ticket's own prediction that
  the geometry would move was WRONG and is corrected in its answer: `n` is declared, so every
  derived quantity is bit-identical -- except the put and receiving crews, which size on lines.
  USER DECISION: 40 / 41 / 42 ruled OUT OF SCOPE, 43 re-scoped to record the era and needing no
  run of its own (this run is its confirming gate). Also: Modern Standby killed the first
  attempt at the analysis stage (STATUS_IN_PAGE_ERROR); `--resume` recovered it in five minutes
  with all eight arm DBs intact.

## Not yet specified

- **Whether the aisle-split axis still asks its old question.** 23 found that decision 9's
  inflation changed what a split arm trades: aisles for travel, not capacity for travel (it
  used to raise fill by shrinking the shelf under a fixed stock, and `cells._tightest_split`'s
  rationale assumed that). A `ks` sweep is therefore not comparable to an archived one, and
  whether the axis should keep the inflation, opt out of it per arm, or be re-framed as an
  aisle-count cost is a decision nobody has made -- dim until an inbound or layout effort
  wants to run one.
- **Ranked arms' steady-state placement.** 13 found FIFO drifts to the class-uniform smear; a
  ranked restock keeps its placement concentrated, so its initial-placement expectation is a
  proxy, not a steady state. Whether the audit's per-arm band should follow the placement as it
  evolves (re-stamped per reorder cycle) or stay at the initial map is dim until a ranked arm
  runs a full replenishment cycle under the era. **26 (2026-09-08) added the PUT side to this
  patch and made it the sharper half**: the pick band is already re-centred per arm
  (`equilibrium.arm_expectations` over a stamped `expected_pick`), but the put band has **no
  per-arm re-centring at all** -- it is class-uniform for every arm. That was invisible while
  every era run was `fifo`, whose restock picks a uniformly random free bin and so IS the
  class-uniform assumption (which is why 26's per-channel price agreed with the run to 0.4% /
  2.8%); a ranked arm's put destinations are not class-uniform, and ADR-0003's empty-bin-first
  rule concentrates them further. The question is whether put needs a twin of
  `arm_expectations` -- and, unlike picking's, the put pricer's `initial` branch cannot serve
  that role as written. Still dim for the same reason: no ranked arm has run a full
  replenishment cycle under the era.
- **Interaction-effects reporting beyond the bands** — the user's framing names
  "interaction effects between departments"; the bands capture equilibrium, but how the
  coupling itself is surfaced (receiving throttles put-away throttles availability
  throttles picks — a lag/propagation read, not just levels) is dim until the era exists.
  04 left one sharp edge of it: whether a CAPPED day on a campaign arm is also a comparison
  caveat (the arm did not deliver the declared throughput) is reported, not yet judged.
- **A derived band for the supply level.** 30 gave the labour clause a band the declared law
  implies (`staffing.cut_share_sd` over the window) but left the supply level's at a typed 0.02:
  the fill closed form stamps a point, and the spread of a finite window's first-attempt share
  over the section's SKU mix (memory `window-mix-before-model-error`) has no closed form on the
  record yet. Dim until a leaf reads near the edge of the typed band; on the 2026-09-08 pair both
  sit within 0.004 of it.

- **A trajectory band for the free-index depth.** 32 left the per-bucket depth REPORTED against
  its stamped setup free, judged only through the events (top-up, spill, repack all zero). Once
  [Derive the stationary fragmentation closed form](issues/34-derive-the-stationary-fragmentation-closed-form.md)
  stamps E[extra bins] per bucket -- and, if it falls out, the transient -- the window's drawdown
  per bucket can be judged against the expected drawdown for those days, the way the labour clause
  is judged against `cut_share_sd`. Dim until 34 says whether the transient has a closed form or
  only the stationary level does.
- **A derived minimum headroom.** 35 floors every bucket's free share at `MIN_HEADROOM = 0.05`,
  an assumption covering what the fragmentation chain does not model (the supply jitter
  `max(1, round(N(lot, lot · cv)))`, a tier spill or own-bin top-up judged at zero, two lines
  for one SKU in one batch). The jitter's law is stamped on the SKU (`supply_cv`), so the bins a
  rounded-up lot needs beyond the expectation may have a closed form per bucket, the way the
  stationary extra does; dim until a run under the derived fill shows a bucket's free index
  crossing the 5% floor (22 of 51 reference buckets sit on it).

## Out of scope

- **The draw-probability form** (user, 2026-09-12, on
  [Re-take the reference run under v3](issues/46-retake-the-reference-run-under-v3.md)'s
  measurement). [Characterise the draw probability](issues/40-characterise-the-draw-probability.md),
  [Gate the form on the generator](issues/41-gate-the-form-on-the-generator.md) and
  [Land the draw probability through every closed form](issues/42-land-the-draw-probability.md)
  are CLOSED out of scope: `p_s` had exactly one consumer, correcting a fill law that was
  under-predicting the realized miss 4x, and the law now reads in band on both leaves with no
  correction. The record does still misprice its own prior-line event on the GENERATOR -- over
  on fulfillment by 16%, under on the store by 19%, in opposite directions -- but the
  instrument accepts the realized result, and a two-sided ~18% error on an intermediate
  quantity does not earn three tickets and a comparability break. 40's built module
  (`Optimization/simdriver/draw_probability.py`, 13 green tests) stays in the tree as the
  sampler effort's starting point; its two `_drawp_*.npz` artifacts are v2 archive, not input.


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
- **Calibration simulations of any kind** (user decision 2026-09-06). The reference run, its
  window amendment
  ([Fit the reference window to the replenishment cycle](issues/11-fit-the-reference-window-to-the-replenishment-cycle.md))
  and its re-take ([Re-take the reference run](issues/12-re-take-the-reference-run.md)) are
  closed: every constant is a closed-form expectation over the known inventory distribution and
  the runtime geometry (13). The six passes 09 left on disk are a one-time correctness check
  for the formula, never a pipeline step.
- **Sampling and inventory generation as an effort of its own** (user, 2026-09-10, while
  resolving [Declare the coverage against the inbound lead](issues/36-declare-the-coverage-against-the-inbound-lead.md)).
  The hypothesis: there may be an implicit bias toward SKUs whose generated attributes let
  them edge out better pick times through an equilibrium of emergent properties of the SKU in
  the catalogue -- the lead attribute now being one more such attribute. A future charting
  session's seed, not this map's route; it would also own re-denominating the catalogue's lead
  attribute in days (today batches, converted at the record by 36 decision 4), which the
  "lead-time denomination" entry above already keeps off this map.
  **Extended 2026-09-12** (user, while working
  [Close the fulfillment fill-law gap](issues/38-close-the-fulfillment-fill-law-gap.md)):
  re-weighting the affinity lift so the sampler preserves the declared marginal line share is
  REJECTED and belongs here too. The record models the demand the simulation generates, not the
  reverse; and re-weighting would invalidate the affinity structure the fulfillment channel exists
  to exercise, on every archived run.
  **Extended again 2026-09-12** (while resolving
  [Measure the repeat structure and the realized lead distribution](issues/39-measure-the-repeat-structure-and-lead-distribution.md)):
  the v2 sampler suppresses a same-SKU line at lag 1 by ~14-16% against a count-preserving
  permutation, and returns the displaced mass at lags 2-3 -- measured twice, on two estimators,
  over the whole 40 days (49,388 fulfillment pairs) and the 20-day window. Batch-size
  autocovariance accounts for at most 1.6% of it, and each batch draws from its own
  `random.Random(seed_batches + i)`, so consecutive per-batch seeds are the untested suspect.
  It is not this map's business: it moves the miss DOWN, so it explains nothing about the fill
  gap, and characterising it belongs to the sampler effort above.
  **CARVE-OUT, 2026-09-12** (user decision, while building
  [Characterise the draw probability](issues/40-characterise-the-draw-probability.md)): a
  correctness DEFECT in the sampler is this map's business, even though the sampler's DESIGN is
  not. The v2 sampler re-draws SKUs it has already selected, so fulfillment batches deliver
  **8.64% fewer distinct lines than the era declares** (40/40 batches; v1 produces zero duplicates
  on the same three) -- which means the declared `n` and the crew derived from it are over-stated
  on that leaf, and "throughput in equilibrium" is the destination itself. Pulled in as
  [Fix the sampler's duplicate draws as v3](issues/44-fix-the-sampler-duplicate-draws.md), to land
  as a NEW sampler version so v1 and v2 stay byte-reproducible. The boundary is unchanged for
  everything else: re-weighting the lift, the frequency profiles, and characterising the sampler's
  intended behaviour all stay out. **The lag-1 suppression above is now a suspect rather than a
  mystery** -- a duplicate draw consumes a slot another SKU would have taken -- and
  **RESOLVED 2026-09-12: it was the defect.** Under v3 fulfillment's lag-1 lift is 1.0138x,
  inside the permutation control's own spread, against 0.8383x under v2 -- a duplicate draw was
  consuming a slot another SKU would have taken, exactly as 44 suspected. What is left here for
  the sampler effort is smaller and DIFFERENT: the STORE's lag structure did not settle, going
  0.846 / 1.808 / 1.657 to 0.848 / 0.842 / 3.022, lag 3 rising as lags 1-2 fall. The counts are
  small (121 co-occurring pairs against a permutation expectation near 40) but the excess is
  many sd. It moves the store's aggregate barely -- 1.09x its share-law-true control against
  1.59x under v2 -- so it is too small to carry a fill-law argument either way, and consecutive
  per-batch seeds remain the untested suspect.
  **SEED MATERIAL, 2026-09-12** (measured while resolving 44, and left here rather than
  ticketed, because it is about the sampler's DESIGN and not a defect): the lift model
  compounds without bound. Affinity lift is **2.8-5.0, median 4.5**, over a **median 35
  partners**, and every already-selected partner multiplies it in again, so `lift_mult`
  reaches **7.8e20** against base frequencies of ~1e-6 -- and from roughly step 500 of a
  fulfillment batch a SINGLE SKU holds **30-89% of all live draw mass**. v1 and v3 implement
  that faithfully; it is the declared model, not a bug. Whether a conditional-demand weight
  should be able to concentrate a batch that hard is a real question for the sampler effort,
  and it is also why 45 must separate the defect from genuine affinity concentration rather
  than assuming 39's -24.5% survives.
