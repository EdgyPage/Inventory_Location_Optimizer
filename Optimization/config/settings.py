"""settings — every tunable a run has, as a flat list of named values.

The authoring surface. One name per knob, at module level, grouped by what it governs and
annotated with what changing it costs. `sim_config.CONFIG` is DERIVED from this file, so
this is the one place a value is written down and the nested dict is a view of it.

    settings.py          you edit this                        (declaration)
        |
        v
    sim_config.CONFIG    the nested dict every reader knows   (run state)
        |
        v
    resolve at run time  + CLI flags + the what-if cell       (this run)

## Reading this file

Every value below is a DEFAULT. Three things override it, in order:

1. a CLI flag (`--n-batches`, `--seed-world`, …) — see `run_simulation`
2. a what-if cell, for the three axes it sweeps — see `simdriver.cells`
3. a `--resume`, which restores whatever the original run recorded in `run_spec.json`

Overrides are written into `CONFIG` and read back through the accessors in `sim_config`
(`n_batches()`, `seed_world()`, …). **Never snapshot one of these at import**: a module
scalar cannot see a CLI override, which is a bug this project has now shipped twice — once
for `_INITIAL_FILL`, which made a run misreport its own sizing in `warehouse.db`, and once
for five more that were caught before they could.

## What does NOT live here

- **Pick-config coefficients.** Those are a swept AXIS, not a setting: each variant is a
  self-registering module under `Optimization/simconfig/configs/`, and the run sweeps all of
  them. A number that varies within one run is not a default.
- **What-if cell definitions.** `Optimization/config/whatif_config.SPECS`.
- **Restock-rule membership.** `Optimization/config/strategies`.
- **Anything derived.** `checkpoint_every` is computed from `n_batches`; the shift's
  hour-length is `SHIFT_SECONDS / 3600`. A derived value declared beside its input is two
  things to keep in step.

Adding a setting: declare it here, thread it into `CONFIG` below, and — if it changes
results — give it a CLI flag, record it in `run_spec.json`, and restore it in both
`_apply_run_spec` and `run_analysis._apply_run_shape`. Miss one of those four and the flag
is accepted and then silently ignored; `Tests/unit/test_run_shaping_params.py` is the guard.
"""
from __future__ import annotations

from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS

# ── determinism ──────────────────────────────────────────────────────────────────
# The two values that decide whether two runs are COMPARABLE. Same world seed = same
# warehouse and catalogue; same batch seed = same demand stream. Changing either makes a
# run incomparable with every run before it, which is why both have CLI flags and both are
# recorded in run_spec.json.

SEED_WORLD = 42            # warehouse + catalogue construction
SEED_BATCHES = 1337        # base of the batch stream; each channel adds its own offset

# ── run shape ────────────────────────────────────────────────────────────────────

N_BATCHES = 100            # batches per arm.  --n-batches; 0 is rejected, not ignored
WORKERS = 1                # pool size.  Changes wall-clock only, never a result
MAX_SKUS = None            # global input-catalog cap; None = no cap.  Preserves the mix
CHECKPOINT_FRAC = 0.1      # flush every ceil(N_BATCHES * this) batches
KEYFRAME_INTERVAL = 25     # batches between viewer keyframes.  Audit points, not accuracy

# Batch-sampler VERSION, and therefore a RESULTS ERA rather than a tuning knob: v2 runs are
# not row-comparable with the pre-flip archive.  Batch caches are fingerprinted per sampler
# so the two eras cannot contaminate each other.  `--sampler v1` is the escape hatch.
SAMPLER = 'v2'

# ── the clock ────────────────────────────────────────────────────────────────────
# A REPORTING FRAME over a continuous clock, not a scheduler.  It labels
# `work_events.shift_index`; nothing dispatches against it, work does not pause at the
# whistle, and a task spanning a boundary is recorded under the shift it started in.

SHIFT_SECONDS = DEFAULT_SHIFT_SECONDS      # 8 hours, in the sim's own unit

# ── crews ────────────────────────────────────────────────────────────────────────
# Imported from simconfig.constants rather than restated: the self-registering pick-config
# modules need these too and importing sim_config from them would cycle.

from Optimization.simconfig.constants import _FF_PICKERS, _STORE_PICKERS  # noqa: E402

STORE_PICKERS = _STORE_PICKERS         # machine order-picker pool
FF_PICKERS = _FF_PICKERS               # human-walker pool
PUT_CREW_SIZE = 1                      # one walker; put-away is not yet a swept axis

# ── per-channel storage and demand ───────────────────────────────────────────────
# Store and fulfillment are INDEPENDENT sections of one warehouse: each sweeps its own
# pick-configs, runs its own restock suite, and writes its own DB subtree.  They are
# combined only after analysis, by run_channel_rollup.

STORE_CART = 'StoreCart'
FF_CART = 'FulfillmentCart'

STORE_FILL = 0.85                      # sizing headroom; --store-fill
FF_FILL = 0.85                         # --ff-fill

STORE_BATCH_MEAN = 0.15                # batch size as a fraction of the channel's SKUs
STORE_BATCH_STD = 0.05
FF_BATCH_MEAN = 0.20
FF_BATCH_STD = 0.05

# Velocity zoning: the fulfillment experiment axis, off by default.  A cell turns it on.
ZONING_OFF = {'enabled': False, 'n_bands': 3, 'mode': 'equal',
              'abc': {'mass_thresholds': [0.7, 0.9]}}
