# Build the picker staffing seam

Type: task
Status: open
Blocked by: 03

## Question

Pickers are the ONE declared staffing input of the calibrated era
([Define the calibrated era](01-define-the-calibrated-era.md)), and they are the one
department with no runtime knob at all: `_STORE_PICKERS = 25` / `_FF_PICKERS = 20` are
compile-time constants in `Optimization/simconfig/constants.py`, re-exported as
`settings.STORE_PICKERS` / `FF_PICKERS`, read into `CONFIG['channels'][<ch>]['num_pickers']`,
with no CLI flag, no run-spec record, and no `_shared` carriage (assets survey, `picker_knobs`).

Build the seam: per-channel picker count as a declared knob with the full five-seam contract
(memory `config-knob-has-five-seams`) — declare in settings (the constants become defaults),
CONFIG + a call-time spec accessor on the `recv_crew_spec` pattern (NOT the `put_crew_spec`
pattern, the documented accepted-and-ignored trap), a CLI flag per channel, run-spec record
restored in BOTH `_apply_run_spec` and `run_analysis._apply_run_shape`, carried in
`workunits._shared`. Analysis already reads `k_pickers` from run params
(`Performance_Evaluations/core/context.py`, fallback 25); that fallback goes once the record
exists, per memory `config-is-not-a-channel-to-an-evaluation`.

Blocked by [Design the staffing record](03-design-the-staffing-record.md) because the record's
shape decides how the knob is recorded — do not build a record 03 then redesigns. The
per-pick-config override (an arm's own `num_pickers` entry) stays as the arm-level escape
hatch.

Resolves when a real (spawn-pool) run declares picker counts on the command line and its run
spec, sim_result, and every worker agree.
