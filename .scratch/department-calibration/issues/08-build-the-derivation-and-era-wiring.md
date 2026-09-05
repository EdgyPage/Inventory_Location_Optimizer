# Build the derivation, the calibration record, and the era wiring

Type: task
Status: open
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
