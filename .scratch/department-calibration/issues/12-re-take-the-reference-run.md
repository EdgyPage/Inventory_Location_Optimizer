# Re-take the reference run

Type: task
Status: resolved
Blocked by: 11

AFK once unblocked. The deliverable of [Take the reference run](09-take-the-reference-run.md)
-- a MEASURED calibration record committed at `Optimization/simconfig/calibration_record.json`
-- under the procedure as amended by
[Fit the reference window to the replenishment cycle](11-fit-the-reference-window-to-the-replenishment-cycle.md).

## Question

Run the amended procedure and commit the record. Everything 09 learned about the launch holds:

- `python -m Optimization.run_reference ... -- --profiles-dir <one-pair view>`; the one-pair
  view (the suite's `profile_layout.json` plus a junction to the `bell_lt0` pair, beside the
  catalogue tree under `PROFILE_INPUT_DIR`) keeps the run on ONE inventory pair without touching
  the suite. If 11 regenerates the catalogue, build the view on the new suite.
- Pass 0 may seed from the last continuation candidate on 09 (`--seed-record`), not from the
  analytic seed -- it is several contractions closer to the fixed point.
- Check the `[staffing]` lines for `saturated` / `clamped` before trusting a pass; confirm
  `reorder_placements` inside the window is at steady state (reorder units per day ~ picked units
  per day, flat across the window halves) -- the exact thing 09's window never carried.
- `--install` copies a MEASURED final record over the committed one; committing it is the
  human's act. Run output stays out of git.

The answer records the measured constants, travel shares, `K_max` per channel, the pass count,
whether the fixed point converged or was cut off, and the receiving self-check -- the same
answer 09 was to give. On resolution the inbound map's
[Verify the derived receiving crew under arrivals](../../inbound-optimization/issues/23-verify-the-derived-receiving-crew.md)
and
[Re-size the funnel in site days](../../inbound-optimization/issues/24-resize-the-funnel-in-site-days.md)
unblock.

## Answer

CLOSED OUT OF SCOPE 2026-09-06, never started. Superseded by
[Derive the expected-travel closed form](13-derive-the-expected-travel-closed-form.md): the
constants are computed at setup from the built geometry and the inventory distribution, so
there is no reference run to re-take and no measured record to commit. The six passes 09 left
on disk remain a development-time correctness check for 13, not a calibration step.
