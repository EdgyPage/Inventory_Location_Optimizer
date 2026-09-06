"""Leaf constants shared by the pick-config modules, sim_config, AND the staffing records.

Kept here (importing nothing from sim_config) to break the circular-import trap: sim_config
imports the simconfig registry, which import_all()s the config modules, so anything both
sides need has to live below both of them.

`_STORE_PICKERS` / `_FF_PICKERS` are the DEFAULTS of the two declared picker knobs -- the
one staffing input of the calibrated era (.scratch/department-calibration, "Define the
calibrated era").  They reach a run through `settings.STORE_PICKERS` / `FF_PICKERS`, then
`CONFIG['global']['store_pickers']` / `['ff_pickers']`, and are read at CALL time by
`sim_config.channel_pickers(name)` -- never snapshotted, never restated.  The pick-config
modules under configs/ no longer name them at all: a per-arm `num_pickers` that disagrees
with the channel's declared count raises at setup (`workunits._channel_runs_for`), so the only
thing a module could legally say is the channel value, and saying nothing says exactly that.
"""

_STORE_PICKERS = 25      # machine order-picker pool size (store channel)
_FF_PICKERS    = 20      # human-walker pool size (fulfillment channel, independent of store)

#: Where a recorded value came from.  ONE enum, shared by the run-spec staffing record and the
#: committed calibration record (.scratch/department-calibration, "Design the staffing record",
#: decision 5): `assumed` = a settings default nobody chose; `declared` = set by a flag or a
#: spec; `seed` = an analytic starting guess; `measured` = read off a reference run;
#: `derived` = computed from other recorded values.  A value KEEPS its provenance as it is
#: copied from one record to the other.  Lives in this leaf because both records' modules sit
#: under simconfig/ and sim_config imports them, so neither may import sim_config back.
PROVENANCE: tuple[str, ...] = ('assumed', 'declared', 'seed', 'measured', 'derived')
