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
  - **No bespoke conversions implicit in the inventory.** A standing preference from the
    same decision: nothing authored on the catalogue may carry an implicit batch or day (the
    coverage-in-generation-batches trap of 09). Stock coverage is per SKU in days of its own
    expected demand; the method must scale to any item distribution unchanged.
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
- **Put-away fills an empty bin first** (decided 2026-09-07,
  [Let a base-stock top-up reach the shelf](issues/20-let-a-base-stock-top-up-reach-the-shelf.md),
  ADR-0003): a top-up consolidates into the SKU's own bin only when no empty bin fits, ahead of
  the rescues and of pending -- the new-bin decision is where optimisation happens. The rescues
  are receiving work (inbound does the repacking), priced and recorded; expected repacks are
  stamped zero. Picks drain a SKU's smallest bin first.

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

## Not yet specified

- **Re-reading the check.** Once
  [Build the empty-first top-up](issues/24-build-the-empty-first-top-up.md) and
  [Field the requirement](issues/23-field-the-requirement.md) resolve (22 landed 2026-09-07,
  unblocking 23), ONE 40-day run on the
  reference pair re-reads 17's check (21 resolved 2026-09-07: the store leaf's audit renders
  again, so that run's store audit will be read, not raised). 19 settled that the floor is a promise, so the
  fulfillment warehouse WILL grow (demand sizing alone took it 977 -> 1,232 aisles; exact
  fielding is 23's number to report); which arms is still dim. Until then the era's numbers on both sections are provisional and the inbound
  funnel's lift ([Sequence the inbound funnel](issues/05-sequence-the-inbound-funnel.md)) has
  not happened.
- **Ranked arms' steady-state placement.** 13 found FIFO drifts to the class-uniform smear; a
  ranked restock keeps its placement concentrated, so its initial-placement expectation is a
  proxy, not a steady state. Whether the audit's per-arm band should follow the placement as it
  evolves (re-stamped per reorder cycle) or stay at the initial map is dim until a ranked arm
  runs a full replenishment cycle under the era.
- **Interaction-effects reporting beyond the bands** — the user's framing names
  "interaction effects between departments"; the bands capture equilibrium, but how the
  coupling itself is surfaced (receiving throttles put-away throttles availability
  throttles picks — a lag/propagation read, not just levels) is dim until the era exists.
  04 left one sharp edge of it: whether a CAPPED day on a campaign arm is also a comparison
  caveat (the arm did not deliver the declared throughput) is reported, not yet judged.

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
- **Calibration simulations of any kind** (user decision 2026-09-06). The reference run, its
  window amendment
  ([Fit the reference window to the replenishment cycle](issues/11-fit-the-reference-window-to-the-replenishment-cycle.md))
  and its re-take ([Re-take the reference run](issues/12-re-take-the-reference-run.md)) are
  closed: every constant is a closed-form expectation over the known inventory distribution and
  the runtime geometry (13). The six passes 09 left on disk are a one-time correctness check
  for the formula, never a pipeline step.
