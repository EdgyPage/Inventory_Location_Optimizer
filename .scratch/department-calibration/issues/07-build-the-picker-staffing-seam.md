# Build the picker staffing seam

Type: task
Status: resolved
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

## Comments

2026-09-05, from resolving [Design the staffing record](03-design-the-staffing-record.md): UNBLOCKED.
The record's shape for this seam: two flat global keys, `store_pickers` and `ff_pickers`, declared in
settings with the compile-time constants as their defaults, spliced into one `STAFFING_KEYS` list that
record and both restore sites iterate (the `INBOUND_KEYS` precedent), exposed through `staffing_spec()`
(inputs only, call-time read), and carried in `workunits._shared`. `CONFIG['channels'][<ch>]['num_pickers']`
becomes a call-time read of the global key. The "arm-level escape hatch" line above is SUPERSEDED: a
per-pick-config `num_pickers` that disagrees with its channel's declared count raises at setup; one that
restates it stays legal. The sixth seam stamps the whole `staffing` dict onto `sim_result`, which is
where the `k_pickers` fallback in `Performance_Evaluations/core/context.py` goes to die.

## Answer

Resolved 2026-09-05. LANDED on `develop`, in the record's shape from
[Design the staffing record](03-design-the-staffing-record.md), and verified by a real
spawn-pool run whose run spec, every leaf, every arm's run params, every worker's crew and the
analysis stage all agree on the declared counts.

**The seams, as built (file anchors are HEAD at resolution):**

1. **Declared** -- `settings.STORE_PICKERS` / `FF_PICKERS` are now the DEFAULTS of two knobs
   (flag names beside them); the values stay in the leaf `Optimization/simconfig/constants.py`,
   which also gained the shared five-valued `PROVENANCE` enum (03, decision 5) -- a leaf because both
   staffing records' modules sit under `simconfig/` and sim_config imports them, so neither may import
   sim_config back.
2. **CONFIG + accessor** -- two flat global keys `store_pickers` / `ff_pickers` on the spliced
   `sim_config.STAFFING_KEYS`; the channel dicts carry NO `num_pickers` any more (one live home, a
   module-level `_PICKERS_KEY` table maps channel to key). `channel_pickers(name)` reads CONFIG at call
   time (a None from a pre-record spec resolves to the leaf default; a non-positive count raises; an
   unknown channel is a KeyError, not a share); `staffing_spec()` returns the INPUTS dict; `k_pickers()`
   is now `channel_pickers('store')` for the diagnostics.
3. **CLI** -- `--store-pickers N` / `--ff-pickers N` (`_positive_int`, default FROM CONFIG), written back
   unconditionally by iterating `STAFFING_KEYS`.
4. **Recorded and restored at both sites** -- one nested `staffing` run-spec key with `inputs`
   (`staffing_spec()`, post-overlay) and `provenance` (`declared` when the flag was typed, else
   `assumed`, from the parser's `explicit` set). `_apply_run_spec` flattens `staffing.inputs` beside the
   flat keys and splices `*STAFFING_KEYS` into its loop; `run_analysis._apply_run_shape` restores the
   inputs into CONFIG and keeps the block verbatim in `_RUN_STAFFING` for the stamp. The structural
   recorded-vs-restored scan in `test_run_shaping_params` now covers the family.
5. **Payload** -- `workunits._shared['staffing'] = staffing_spec()`; the worker sizes its crew from
   `k_pickers` (unchanged) and `strategy_runner._check_declared_crew` refuses a payload whose sized crew is
   not its declared crew (module-level so a test can hand it a payload).
6. **Stamped onto sim_result** -- `run_analysis._sim_result_from_meta` stamps `channel` and the WHOLE
   `staffing` record (the run's own, or a reconstruction marked `assumed` for a pre-record run);
   `EvalContext.k_pickers` reads its own channel's count off it and the literal-25 fallback is gone
   (`k_pickers` left `_SLIM_KEYS` and `sim_assets`). That fallback was the store's crew on every
   FULFILLMENT leaf; no evaluation read it yet, so it was a latent wrong number, not an output
   defect -- the throughput audit will be its first reader.

**The disagreement rule** (03, decision 1): `workunits._channel_runs_for` raises at setup when a
pick-config module's own `num_pickers` differs from the channel's declared count; restating it stays
legal. Consequence: the four committed modules (`store`, `store_high_height`,
`store_high_weight_high_height`, `ful_calibrated`) DROPPED their restated constant -- a literal 25 would
have made `--store-pickers 7` raise on every arm, so the only thing a module could legally say is the
channel value, and saying nothing says exactly that.

**Verification.** `python -m Optimization.run_simulation --store-pickers 7 --ff-pickers 5 --workers 4
--n-batches 3` on a 300-SKU mixed catalogue (68 arms, 4 spawned workers):

| surface | store | fulfillment |
|---|---|---|
| `run_spec.json` `staffing.inputs` (both `declared`) | 7 | 5 |
| leaf `config.json` `num_pickers` | 7 | 5 |
| `simulation_runs` rows `num_pickers` = `k_pickers` (34 + 34 arms) | 7 = 7 | 5 = 5 |
| `picker_events` max `picker_id` (the crew that actually picked) | 6 | 4 |
| analysis stage (54 jobs, 4 workers) | no evaluation raised | |

Byte-identical discipline: with no flag the values are 25 / 20 marked `assumed`, `config.json` and the
run params keep their keys, and 1603 unit tests plus the e2e/integration subset are green (one smoke
test that pinned the superseded per-arm override was rewritten to the new rule). Preflight's two
canaries: tree shape unchanged, source fingerprint refreshed.

**A trap found on the way** (recorded as memory `pool-run-swallows-dead-arms`): the first pool run
died in every worker on a KeyError, and the harness logged "these arms produced no data", ran the
analysis over nothing, and exited 0. `Config stage: 0 job(s)` after a sim is the tell; read the
tracebacks in `run.log`, never the exit code.

**Not built here, by decision:** the `put_crew_spec` trap fix rides
[the derivation build](08-build-the-derivation-and-era-wiring.md) (03, decision 7); an explicit picker
flag on `--resume` still follows the generic "explicit wins with a note" policy, which 08 tightens once
the recorded `derived` block is authoritative (03, decision 3).

**What 08 inherits:** extend `STAFFING_KEYS` and `staffing_spec()` with the scalars and the put crew
mode (every seam iterates the list, so a new input is recorded, restored and carried by construction);
add the `derived` sub-block beside `inputs` / `provenance` in the run-spec record and the
`calibration_stale` / `K_max` stamps; `_staffing_record()` in run_analysis already stamps whatever the
record holds; `PROVENANCE` is in `simconfig/constants.py` for the calibration record to share.
