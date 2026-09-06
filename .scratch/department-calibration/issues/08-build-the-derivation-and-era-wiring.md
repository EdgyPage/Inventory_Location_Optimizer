# Build the derivation, the calibration record, and the era wiring

Type: task
Status: resolved
Blocked by: 03, 06, 07

Blocked by [Design the staffing record](03-design-the-staffing-record.md) because the record's
field names and seams are the derivation's inputs and outputs; by
[Add the per-item charge and break the cost model](06-add-the-per-item-charge.md) because the
derivation's intercept scales and per-item ratio are 06's knobs; and by
[Build the picker staffing seam](07-build-the-picker-staffing-seam.md) because the derivation's
one declared input has to be settable.

## Question

Land, on `develop`, everything a reference run needs to be launchable and everything an era run
needs to derive its staffing live at setup:

1. **The derivation** — the chain in 01's answer (pick capacity → daily demand → batch content →
   put load → put crew → receive load → receiving crew), a declared scalar at every step with 01's
   defaults (ρ 0.85 each, f 1.0 each), inputs AND derived outputs written to the run spec,
   computed at setup so no run carries a stale literal. The declared batch fractions
   (`STORE_BATCH_MEAN` / `FF_BATCH_MEAN`) no longer drive an era run; whether they survive for
   continuous (non-era) runs the build decides and records in its commit message.
2. **The calibration record loader** — 02's committed record under `Optimization/simconfig/`
   (constants + provenance) loaded as `settings.py` defaults, per-constant CLI overrides, the
   warehouse-fingerprint staleness check that WARNS and stamps `calibration_stale` into the run
   spec, and the `K_max` exceedance warning + stamp. Until 09 has run, the record holds the
   analytic pass-0 seed with `provenance: seed`, so the loader is exercised before any
   measurement exists.
3. **The era wiring** — a CLI flag for `SHIFT_DRAIN_OR_CAP` (none exists today), and
   `drain_or_cap` + `releases_per_day=1` + `roll_over_unpicked` + the forced cut as the campaign
   specs' defaults.

Every new knob rides all five seams (memory `config-knob-has-five-seams`) plus the sixth stamp
onto `sim_result` for anything an evaluation will read (`config-is-not-a-channel-to-an-evaluation`).

**Tests.** The derivation is arithmetic from declared inputs — assert the chain against a hand
computation, including the ceilings. The stale stamp fires on a fingerprint mismatch and stays
silent on a match. The `K_max` warning fires above the recorded bound. The era defaults reach a
spawned worker (`_shared`), the spawn trap. The provenance field distinguishes `seed`, `measured`
and `derived` and a run spec records which one it ran under.

Resolves when a reference run per 02's procedure can be launched from the command line on
`develop` with the tests green.

## Comments

2026-09-05, from resolving [Design the staffing record](03-design-the-staffing-record.md): the shape
this build lands. (1) The derivation is a PURE module, `Optimization/simconfig/staffing.py`: inputs +
the loaded calibration record + the script's totals in, the derived dict out (01's table is its
interface); it runs at setup AFTER batch precompute because the receiving crew needs the packs the
script implies. Test it without a run. (2) The run spec records one `staffing` key with `inputs` and
`derived` sub-blocks; derived values are never CONFIG keys. (3) Restore reads the recorded `derived`
block as authoritative and re-derives from the restored inputs: a disagreement raises on resume,
warns and stamps on re-analysis. (4) Errors under the era: any explicit legacy crew flag
(`--recv-crew-size`, the base put crew size, the split family's three crew counts) and
`put_queue_split` on. Flag-off, all of them keep working verbatim. (5) One provenance enum shared with
the calibration record: `assumed` / `declared` / `seed` / `measured` / `derived`; scalars carry
`assumed` or `declared`, copied constants keep theirs, the derived block is `derived`. (6) The `put_crew_spec`
trap fix (PUT_CREW_SIZE / PUT_CREW_MODE gain real CONFIG keys and every seam) rides here; PUT_CREW_MODE
is a DECLARED input on the record. (7) `_sim_result_from_meta` stamps the whole `staffing` dict as one
key, including `calibration_stale` and the `K_max` stamp.

2026-09-05, from resolving [Declare the equilibrium bands](04-declare-the-equilibrium-bands.md):
three riders. (a) Persist the drain-or-cap ledger as a `shift_days` table (run_id, day, cap_end,
end_s, drained, standing, last_finish), written as each close-out fires — today it is a log line
only — riding the schema pipeline (`schema-maintainer`); the close-out fires at the first batch
of the NEXT day, so the final day needs its own flush, outside the checkpoint tail (memory
`run-end-writers-miss-the-final-flush`). (b) The `derived` block gains `expected_utilization` per
department per channel leaf (load × s ÷ (crew × S), after `ceil` and after the leaf's share of the
site crew); the `inputs` block gains `band_tol` (default 0.10, `assumed`). (c) ρ stays 0.85 for all
three departments — the ticket's 0.80/0.90 sketch is withdrawn.

2026-09-05, from resolving [Sequence the inbound funnel](05-sequence-the-inbound-funnel.md): one
rider. Flip the DOCSTRINGS on `PHASE2_RECV_CREW_SIZE` / `PHASE2_RECV_DAY_SECONDS` and the
pilot-gate comment block in `whatif_config.SPECS` — today they teach "size it against the BATCH,
never against a shift", the inverted form of the denomination invariant this map established, and
under the era the two flags they describe RAISE (03, decision 4). Words only: the constants and flags
stay flag-off-live (byte-identical discipline); the `inbound_pilot` spec's new shape is inbound
ticket 23's work, not this build's.

2026-09-05, from resolving [Build the picker staffing seam](07-build-the-picker-staffing-seam.md):
UNBLOCKED (03, 06, 07 all resolved). What this build inherits: `sim_config.STAFFING_KEYS` and
`staffing_spec()` are the INPUTS surface -- extend the list and the dict with the utilization /
replenishment scalars and the put crew mode, and every seam (flags are the one hand-written site,
one per key) records, restores and carries the new input by construction; the run-spec `staffing`
record already has `inputs` + `provenance`, so `derived` and the `calibration_stale` / `K_max` stamps
go beside them and `run_analysis._staffing_record()` stamps whatever the block holds onto
`sim_result`; `PROVENANCE` (the shared enum) is in `Optimization/simconfig/constants.py`, importable
by the calibration-record module without a cycle; `strategy_runner._check_declared_crew` is where a
worker refuses a crew its record did not declare (the derived crews' check belongs beside it); the
`put_crew_spec` trap fix rides here (03, decision 7), as does tightening `--resume` with an explicit
picker flag once the recorded `derived` block is authoritative (03, decision 3).

## Answer

Resolved 2026-09-05. LANDED on `develop`. A reference run per
[Choose the calibration procedure](02-choose-the-calibration-procedure.md) launches from the
command line as

    python -m Optimization.run_simulation --spec calibration_reference --n-batches 40

(one cell, `fifo`, `lpt`, both channels, era on, crews derived), and two smoke runs on generated
300- and 400-SKU catalogues (store-only and mixed) ran end to end: derivation, record, ledger,
analysis stage, no raise. Unit tests green; the two new test files hold 69 tests.

**1. The derivation** is the pure module `Optimization/simconfig/staffing.py`, 01's table as its
interface, in two stages because the script is derived from the pickers and the crews from the
script: stage A from the CATALOGUE (`analytic_pick` -- one intercept per line, the per-item charge
and `qty x handle_var` per unit at ground, frequency-weighted like the sampler; `pick_capacity`,
`daily_demand`, `batch_content`, which turns one day's demand into the sampler's mean fraction and
keeps the declared coefficient of variation, clamping to 1.0 with a `saturated` flag when the crew
asks for more lines than the section has SKUs); stage B from the SCRIPT after precompute
(`script_totals`, `reorder_lot` = the manager's order-up-to rule at the reorder point,
`implied_reorders` packs every expected lot with the sim's own packer and prices put-away
analytically and receiving EXACTLY per pack, `crew_size` = ceil with a floor of one,
`expected_utilization` after the ceiling and the leaf's share of the site crew, `derive`). The
harness seam is `workunits._derive_staffing_for_pair`, run per inventory pair in
`_build_work_units` under `era_on()`, BEFORE any channel-run is prepared: it replaces the
channels' batch fractions (and the store-only pair-level `BatchConfig`), precomputes the batches
through the same `_channel_batch_plan` / `_worker_inventory_args` helpers `_prepare_channel_run`
uses (so the cache is warm and the fingerprints identical), and leaves `shared['staffing']`, from
which the payload's `put_crew` / `recv_crew` are sized (`put_crew_spec(size=)`,
`recv_crew_spec(size=)`) and the payload's `staffing` becomes `{inputs, derived, calibration}`.
The **declared batch fractions survive for flag-off runs unchanged** (byte-identical); under the
era they are only the coefficient of variation the derived content inherits. Derived blocks are
keyed BY PAIR in the run spec (`staffing.derived[<pair label>]`, `staffing.calibration[<pair>]`)
because the catalogue decides the load; a single-pair run has one key.

**2. The calibration record** is `Optimization/simconfig/calibration_record.json` + the loader
`calibration.py`. The pass-0 record is the SEED and holds no numbers: each constant is a
`travel_share` over the catalogue's own analytic prediction (`s = analytic x share`, stamped
`seed`), because no reference run exists and a number would be right for at most one catalogue.
The shares are 1.01 (store) and 1.04 (fulfillment) from the 2026-08-20 archive's fifo arms'
makespan-weighted picking share (0.990 / 0.959, pre-charge), and 1.05 for put-away as a stated
guess -- all `assumed` in the record's notes. The same rule prices a NEW catalogue from a MEASURED
record provisionally (stamped `derived`, 02 decision 7). Resolution is override (`--s-pick-store`
/ `--s-pick-ff` / `--s-put`, `declared`) > recorded value (its provenance) > analytic x share;
`--calibration-record PATH` loads a candidate. The record's constants ride the three
`CALIBRATION_KEYS` on `STAFFING_KEYS` (None = take the record), so they are recorded, restored and
carried by construction. Staleness compares the record's warehouse fingerprint with the run's:
`calibration_stale` (warn + stamp) with `calibration_measured` beside it so "matched" and "never
measured" read apart. `k_max` per channel is in the record (null in the seed); a declared crew
above it warns and stamps `k_max_exceeded`. The `s_recv` the derivation computes exactly from the
script sits in `derived.receiving.s_recv` (`derived`) for 09's self-check.

**3. The era wiring.** `--shift-drain-or-cap` exists (settings, CONFIG, flag, recorded, restored
at both sites, carried as `work_day_spec()['drain_or_cap']`); `era_on()` is the one predicate.
`_check_era_flags` completes the regime (`--releases-per-day` to 1, `--roll-over-unpicked` and
`--cut-at-day-end` to on, each with a note) and REFUSES, with `SystemExit`, the legacy crew flags
typed explicitly (`--recv-crew-size`, `--recv-day-seconds`, `--recv-day-origin`, `--put-crew-size`,
the split family's three crews), `--put-queue-split`, and a cadence other than 1. Specs gained
`run_defaults` (whatif_config.ERA_RUN_DEFAULTS) applied by `_apply_run_defaults` to a NEW run with
an explicit flag winning; `inbound_select`, `inbound_policies`, `inbound_pilot` and the new
`calibration_reference` carry it; `single` / `scheduler_ab` / the canaries stay flag-off.

**Riders landed.** (a) The `shift_days` ledger (04): a new table `(run_id, day, cap_end, end_s,
drained, standing, standing_put, standing_dock, standing_carry, last_finish)` through the schema
pipeline (`--sync` before, `--accept` after; outgoing `be2a593727be` adopted, declared id now
`487a65bf83a9`), written in both checkpoint bundles and with the FINAL DAY flushed by
`save_shift_days` outside the `if pb:` tail; `load_shift_days` / `shift_day_frame`; column
semantics registered. Persisting it exposed a defect in the log-only ledger: the close-out folded
the NEXT day's first batch into the day it closed (day 0's `last_finish` read 2x its cap, the final
day's 0.0). Fixed: the boundary is tested before this batch's clocks, cut and depths are folded in,
and a day closes on the snapshot its own last batch left. (b) `expected_utilization` per department
per leaf and `band_tol` (default 0.10, `assumed`) on the inputs. (c) The `PHASE2_RECV_*` and
`inbound_pilot` docstrings now teach the denomination invariant and say the flags raise under the
era. (d) The `put_crew_spec` trap fix: `put_crew_size` / `put_crew_mode` are CONFIG keys read at
call time, `--put-crew-size` (flag-off only) and `--put-crew-mode` (a declared staffing input on
`STAFFING_KEYS`) exist, both recorded and restored. (e) Resume: `_record_derived` compares a fresh
derivation with the recorded block and RAISES on disagreement (`derived_differs`, floats with a
tolerance); a multi-cell run hits the agreeing branch on every later cell, the free drift check.
(f) `_check_declared_crew` also refuses a put or receiving crew the payload's `derived` block did
not derive. (g) `STAFFING_KEYS` grew to twelve: pickers, `rho_pick/put/recv`, `f_put/f_recv`,
`band_tol`, `put_crew_mode`, the three overrides; `staffing_spec()` resolves a None scalar to its
settings default and keeps a None override.

**Not built, by decision or scope.** The re-analysis-side "warn and stamp" re-derivation (03,
decision 3) needs the batch caches and is left to
[Build the equilibrium check and the throughput audit](10-build-the-equilibrium-check-and-audit.md),
which reads them anyway; `_apply_run_shape` restores the era, the scalars and keeps the whole
`staffing` block (derived + calibration) for the sim_result stamp. The reference run's 40-day
verdict, the equilibrium precondition and the write of a measured record are 09 and 10.

**What the smoke runs showed** (300 and 400 SKUs, 2 pickers per channel, 5-6 days): the seed
prices the store at ~67-70 s/unit and fulfillment at ~3.1 s/unit; two pickers saturate a
134-SKU fulfillment section (clamped, warned); the put and receiving site crews derived to 2 each;
`expected_utilization` per leaf sat well below rho, as 04 predicted; every day but one capped --
the toy catalogues are not in equilibrium, which is exactly what the reference run's precondition
exists to judge, and not this ticket's claim.

**Glossary / docs:** `Optimization/config/README.md` gained the derivation and record rows;
`Optimization/persistence/README.md` names `shift_days`. No new glossary terms: *Era*, *Calibration
record*, *Staffing record*, *Provenance*, *Travel share* already cover everything built.
