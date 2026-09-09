# Declare the demand and derive the crew from the joint first-time confidence

Type: task
Status: resolved

Graduated 2026-09-08 from
[Fit the store's window to its own steady state](27-fit-the-store-window-to-its-steady-state.md),
decisions 4-8. AFK build. Skills: `codebase-design`. Records ADR-0004 (already written:
`docs/adr/0004-demand-declared-crew-derived-first-time-confidence.md`).

## Question

Reverse the era's declared input and re-derive the picking crew from a closed-form first-time
guarantee. Today `Optimization/simconfig/staffing.py` takes pickers per channel as the declaration,
sizes the batch content from `K x S x rho_pick` through `expected_travel.solve_n`, and prices the
load on SERVED units (demand x fill rate). 27 showed that under the era nothing is lost -- every
missed or cut unit is re-offered next day and lead-0 top-ups land before the re-attempt -- so the
crew must pick all of demand, and that the day law is a declared Gaussian on the line count whose
quantile and partial expectation are exact enough to promise on.

**Build:**

1. **The declaration.** Demand per channel is the era's declared input, stored in the sampler's
   native unit (the fraction of the section's SKUs drawn per day; `settings.py` `STORE_BATCH_MEAN`
   / `FF_BATCH_MEAN` are the flag-off ancestors) so it scales to any catalogue. Its value on the
   reference pair is today's derived content -- 588.65 store / 2,903.19 fulfillment lines a day,
   i.e. the `mean_fraction` `batch_content` produced at the fixed point -- so the warehouse, the
   levels and the script family stay where they are. Two flags replace `--store-pickers` /
   `--ff-pickers`, which are REFUSED under the era exactly as the legacy crew flags are (a typed
   crew is a regime nobody derived). All five seams (memory `config-knob-has-five-seams`).
2. **The joint confidence** `first_time_confidence` (0.95, `declared`) replaces `rho_pick` for
   picking only; `rho_put` / `rho_recv` stay. Equal split: each side at `sqrt(c)`.
   - **Shelf side:** solve `floor_lines` (continuous; `coverage.py` `L_s = ceil(floor_lines x
     E[q_s])`) so the stamped first-pass fill clears `sqrt(c)` (~1.26-1.27 lines on the reference
     catalogue, fill 0.9733 at 1.25 / 0.994 at 1.5). Fill is a pure catalogue closed form while
     every SKU sits on the floor; state what happens when a section is NOT fully floored (the fill
     then depends on `coverage_days` too -- solve the same root, the formula already handles it).
     A user-typed `floor_lines` under the era is either refused or accepted only at or above the
     solved value and stamped `declared` -- pick one and say why.
   - **Crew side:** solve the smallest integer crew `K` per channel such that the expected cut
     share `E[(W - K S)^+] / E[W] <= 1 - sqrt(c)`, with `W` the day's expected work over the
     routing chain at the DECLARED demand, the Gaussian line count's cv from the declaration, and
     the partial expectation `sd x [phi(z) - z(1 - Phi(z))]` (or the 7-node Gauss-Hermite sum
     `expected_travel._nodes` already uses, since `expected_pick(n)` is concave in n). Load is
     DEMANDED units, never served. Stamp the expected cut share, the expected utilization (now
     derived, ~0.72 on the store), and `K`.
3. **The record** (`staffing.inputs` / `derived` / `calibration`): demand `declared`, pickers
   `derived`, `first_time_confidence` `declared`, `floor_lines` `derived` (solved), the day law
   (family, cv) stamped beside the sampled script's per-day cv, and the sampled script's actual
   per-day totals priced (27 decision 5) with the guarantee made against the declared law -- both
   stamped, named apart. Restore, resume and the `sim_result` stamp follow 03's contract; a resume
   that re-derives a different crew raises.
4. **The fixed point.** With demand declared, the levels no longer depend on the crew; check
   whether `era_coverage.fixed_point` still needs to iterate (travel depends on geometry, but only
   the crew reads travel now) and simplify if it collapses to one pass.
5. **Tests:** a sabotage test that pricing on served units fails the cut-share bound; the closed
   form's cut share against a Monte-Carlo of the declared law (seeded, tolerance); the refusal of
   the picker flags under the era; the record round-trip; byte-identical store-only path with the
   era off.

## Done when

- The era launches from a demand declaration and a joint confidence, derives both crews and the
  floor, and stamps them with provenance; the picker flags refuse under the era.
- On the reference pair the derived store crew and expected cut share are reported (expected:
  crew ~32, mean utilization ~0.72, cut share <= 0.0253), and the derived floor lands near 1.27
  lines with the stamped fill >= 0.9747.
- ADR-0004 matches what was built (amend it if the build found a reason to deviate, and say so on
  the map).

## Answer

LANDED 2026-09-08 (AFK build). The era launches from a demand declaration and one joint
confidence, solves the line floor and the picking crew from it, derives the two site crews from
the script, and stamps every one with provenance; the picker flags and `--rho-pick` refuse under
the era, the demand flags refuse without it. ADR-0004 amended with the two build decisions
below.

### What was built

1. **The declaration** (all five seams). `store_demand` / `ff_demand` (the sampler's unit: the
   fraction of the section's SKUs drawn per day) and `first_time_confidence` (0.95) are three
   new `STAFFING_KEYS`, flags `--store-demand` / `--ff-demand` / `--first-time-confidence`,
   restored at both sites, carried in the payload. Defaults on the reference pair are the
   previous fixed point -- 0.00245335 x 239,938 = 588.65 store and 0.0181380 x 160,062 = 2,903.2
   fulfillment lines a day -- so the warehouse, the levels and the script family stay put.
   **The regime decides which keys are inputs** (`sim_config.ERA_ONLY_KEYS` /
   `FLAG_OFF_ONLY_KEYS`): `staffing_spec()` records the picker keys and `rho_pick` as None under
   the era and the demand / confidence as None flag-off; `staffing_provenance()` (a new accessor,
   replacing the inline comprehension in `run_simulation`) writes `derived` for the pickers under
   the era and no provenance for a key the regime does not read. `_check_era_flags` refuses the
   other regime's flags in BOTH directions; `_channel_runs_for` refuses a per-arm `num_pickers`
   under the era even when it restates the placeholder.
2. **The crew side** (`staffing.py`, pure): `first_time_split` (`sqrt(c)`), `line_moments`
   (`E[q]`, `E[q²]` off the stamped law), `units_cv` (decision 8's `Var[U] = n·Var[q] +
   (cv·n)²·E[q]²`), `partial_expectation` (`sd·[φ(z) - z(1 - Φ(z))]`), `cut_share`,
   `solve_pickers` (walks up from the crew that fits the mean day to the first K inside the
   bound). The load is `E[W] = n·E[q] × s_pick` -- DEMANDED units at the expected seconds per
   served unit read at the declared day -- under a Normal with the day's unit cv. Validated
   against a seeded Monte-Carlo of the declared law (Gaussian lines, per-line Poisson draws) to
   5% relative on the cut share. On the reference store's own numbers: **K = 32, utilization
   0.720, expected cut share 0.0203 <= 0.0253**; the served-unit derivation would have fielded
   29 with a real cut share of 0.042 -- the sabotage test pins it.
3. **The shelf side** (`coverage.solve_floor_lines`): the fill is a non-decreasing STEP function
   of the floor, so it is bracketed by doubling from one line and bisected to 1e-4 lines, the
   answer rounded UP -- always at or above the true threshold. Per SECTION, at the declared line
   count, before any level is declared (`era_coverage.resolve_floors`); a section not fully
   floored solves the same root through `coverage_days`. Solved 1.2728 (store) / 1.2858
   (fulfillment) lines on the era canary and 1.2779 on the 90-SKU test pair, fill 0.975.
4. **The fixed point collapses** under the era: `seed_lines` returns the declaration, `stage_a`
   prices `expected_pick` AT it and solves the crew, `n_out == n_in` and the loop converges in
   ONE round (a `--max-skus` sample that shrinks a section costs one more). Flag-off the loop
   iterates exactly as before. `floor_lines` defaults to None (`settings.FLOOR_LINES`), out of
   `_SCALAR_DEFAULTS`, "solve it" under the era and one line flag-off
   (`coverage.DEFAULT_FLOOR_LINES`).
5. **`derive`**: the picking load is the SAMPLED SCRIPT's demanded units x `s_pick` (27 decision
   5), `pick_capacity_s` is the granted day `K·S`, `expected_utilization.pick` is DERIVED, `rho_pick`
   is never read; the channel record gains `pickers_provenance`, `pick_load_s`, `demand` (the
   declaration, `lines_per_day`, `units_per_day`, served units, the day law's two cvs) and
   `guarantee` (`first_time_confidence`, `side`, `crew`: `pickers`, `cut_share`,
   `cut_share_max`, `load_s`, `sd_s`, `cv`, `expected_utilization`). The coverage record carries
   `floor_lines` (the INPUT, None when solved), a `floor` block per channel (`floor_lines`,
   `provenance`, the solve's stamp) and `final[<ch>]['floor_lines']`; `era_coverage.floors_at`
   reads it per channel with the scalar fallback for every older record, and
   `declare_from_record` re-declares each section at its own floor.
6. **The ONE crew reader**: `staffing.channel_crew(record, channel=, pair=)` prefers
   `derived.channels[<ch>].pickers` over `inputs.<ch>_pickers`, over both record shapes; the
   worker's `_check_declared_crew`, `equilibrium.expectations_for` and `EvalContext.k_pickers`
   go through it (`picker_key` moved beside it). `_derive_staffing_for_pair` replaces the
   channel's `PickerProfile` and its `PickConfig` with the solved K, so `k_pickers`, the run
   params and the worker's crew all read it; the placeholder `channel_pickers` builds the
   channel and sizes nothing.

### Two decisions the ticket left open

- **A typed `--floor-lines` under the era is accepted at or above every channel's solved value
  (stamped `declared`, the solved one recorded beside it) and REFUSED below it.** A smaller
  floor is a smaller promise than the confidence makes; raising it silently would be the second
  authored knob that moves the crew, the pattern that hid the fill-rate defect.
- **The guarantee's day law is the Normal on the day's UNITS, not the 7-node Gauss-Hermite sum
  over the routing chain.** `expected_pick` already averages the day over the line law, so its
  seconds per unit is the right price; the overflow is then a one-line partial expectation,
  exact for the model up to the compound sum's normality (5% on the toy MC, ~0.001 of cv on the
  reference pair per decision 8).

### Verified

- `Tests/unit`: 1,832 green before the new file; `Tests/unit/test_first_time_guarantee.py`
  adds 32 (closed forms by hand, the sabotage, the Monte-Carlo, the floor solve, both regimes'
  inputs and provenance, the record round-trip through BOTH restore sites, the refusals both
  ways, the crew reader over every shape, the era on a real tiny pair in one round, flag-off
  byte-identical on the same pair). Nine existing tests updated to the new contract.
- `Tests/integration`: 394 green; ONE PRE-EXISTING failure (the run-tree schema's
  `figures_throughput_pngs` attribution omits `throughput.audit`; untouched by this build --
  chip spawned). `Tests/e2e/test_channel_runner_smoke.py`: 4 green.
- Preflight: both canaries (flag-off by design) end to end, tree shape unchanged, fingerprint
  refreshed (`Optimization/schemas/run_tree/INDEX.json`). All seven non-architecture gates green.
- **An era canary** (the preflight's mixed catalogue, `_canary_single`, 200 SKUs, 2 days, demand
  0.05 both channels): floors solved 1.2728 / 1.2858 at fill 0.9748 / 0.9750, crews solved 1 / 1
  (cut share 0.0068 / 0.0000, the day is tiny), put crew 1, receiving 1; every arm's
  `simulation_runs` row carries `num_pickers = k_pickers = 1` -- the DERIVED crew, where the
  placeholder was 25 / 20 -- and every arm completed both batches with no Traceback.

### From the review (code-reviewer, fixed before the commit)

- `solve_pickers` walked up from the crew that fits the MEAN day and so was not the smallest
  feasible crew whenever the spread is small (10.1 days of load at cv 0.02 solved to 11, not
  10; it never bound at the declared cvs). The share is never below the plain excess, so the
  search now starts at `ceil((1 - bound)·E[W] / S)` and the first hit is the minimum; pinned.
- Three readers of the coverage record tripped on the floor's None: the inventory catalog's
  `len(d) == 3` gate (it would have logged "declaration unrecorded" for every run and the two
  site macros would have fallen to "this snapshot does not record"), and `held_fixed.json`,
  where `floor_lines` vanished as a factor. The inventory document now renders a `floor_text`
  (`1 line(s)` / `1.273 (store) / 1.286 (fulfillment) line(s), solved`) that the log line and
  both macros read, and the fixed register reads the per-channel floor from the coverage block.
- The pre-record analysis fallback now takes its provenance from `staffing_provenance(set())`
  rather than a literal; `k_pickers()` says it is the placeholder under the era.
- The architecture layer is stale (three new `staffing` imports, `picker_key` moved, one new
  test file) -- the `architecture-maintainer`'s re-sync, as after every build on this map.

### For 30 and 31

- The stamped expected cut share 30 judges against is
  `staffing.derived[<pair>].channels[<ch>].guarantee.crew.cut_share`; the expected fill is
  `calibration[<pair>].coverage.final[<ch>].fill.fill_rate` (now the solved floor's).
- The reference pair has NOT been re-planned here (the loop costs ~8 minutes a pair and 31 takes
  the 40-day run anyway): its solved floor and crew come with 31's run. Expect ~1.27 lines and
  K = 32 on the store from the closed form above.
- Memory `nothing-is-lost-under-the-era` still says "build pending"; the memory-maintainer should
  amend it to LANDED (29) with 30/31 outstanding.
