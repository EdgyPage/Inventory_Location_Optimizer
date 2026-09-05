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
