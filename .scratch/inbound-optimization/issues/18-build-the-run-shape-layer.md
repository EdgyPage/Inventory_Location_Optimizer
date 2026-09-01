# Build the run-shape layer

Type: task
Status: open
Blocked by: 08

## Question

Build the three run-shape changes the funnel (08) needs, in ONE session against ONE schema
event. They are bundled deliberately: all three touch `SHAPE_SOURCES`, so each would force
its own preflight canary pair, and splitting them leaves states where the tree shape has
moved but its writers have not — the reasoning ticket 07 used when it refused to split 17.

### 1. The fifth `Cell` field — the inbound-policy axis

`Cell` is a 4-field NamedTuple (`name, split, zoning, scheduler`, `simdriver/cells.py`).
Add the inbound axis so phase 2 is one multi-cell run rather than six single-cell runs.
Required because `INBOUND_YARD_POLICY`/`INBOUND_DOCK_POLICY` are whole-run settings, and
because the whole cross-cell comparison surface (`whatif_delta`, `whatif_labor`,
`whatif_volume`) fires only when `len(cell_items) > 1` — six separate runs would produce no
comparison artifact at all.

- The field, plus the loop in `_build_cells` and the value in `Cell.overrides()`.
- **`scenario.py`'s positional 4-unpack breaks** (`for ci, (name, aisle_split, zoning,
  sched) in enumerate(cells, ...)`) — a 5th field raises `ValueError: too many values to
  unpack`. `_apply_cell` takes three positional args and is called with an explicit 3-arg
  spread at two sites; both need the signature change.
- **The cell NAME must carry a suffix.** `_build_cells` dedupes by name, so an inbound axis
  with no suffix silently collapses inbound-on and inbound-off into one cell — no error,
  fewer cells than asked for. This is the trap to write a test against.
- `_apply_cell` writes the inbound keys into `CONFIG['global']`. This composes already:
  `inbound_spec()` reads `CONFIG['global']` at CALL time inside `_prepare_channel_run`, so
  a per-cell value reaches every worker correctly once written.
- Pinned assertions in `Tests/unit/test_cell_record.py` (`len(c) == 4`, the `_fields`
  tuple, a 4-name unpack) move with it.
- The phase-2 spec entry in `whatif_config.SPECS`: ten cells (`fifo` as `reference`,
  `lifo`, `gain_myopic`, `gain_forecast`, `gain_gated` × 3 H points, `futuresight` × 2 w
  points, inbound-off anchor), zoning off (the gain bundle refuses under velocity zoning),
  scheduler fixed at `lpt`, `arms` carrying the selected restock keys.

### 2. The inbound family's deferred seams 3–4

Every knob this effort added (09, 13, 14, 15) declared seams 1–2 and deferred seams 3–4 to
"the first sweep" per 09's precedent. 08 decided what the first sweep is, so the debt falls
due here. **Deferring further is not available**: a phase-2 cell that cannot record the
lead shape and threshold it ran under is not re-analysable, and 07's derive-late fee report
reads the threshold off the run spec (its HEAD-default fallback exists for runs that
predate recording — this campaign must never exercise it).

- CLI flags for the swept knobs (at minimum `INBOUND_LEAD_MINUTES`, `INBOUND_LEAD_SPREAD`,
  `INBOUND_FEE_THRESHOLD_DAYS`, `INBOUND_URGENCY_HORIZON_DAYS`,
  `INBOUND_FUTURESIGHT_BATCHES`, `INBOUND_STANDING_YARD`, `INBOUND_DOCK_DOORS`).
- The run-spec record in `_write_run_spec`, which today records `recv_*` and `put_*` and no
  `inbound_*` at all.
- **BOTH restore sites**: `_apply_run_spec` AND `run_analysis._apply_run_shape`. They are
  separate functions and today neither touches inbound; restoring in one is the silent
  half-fix.
- Record the lead TAG (`0x1EAD`) alongside, per 15's answer: it is un-re-derivable, and a
  comparison spanning a TAG change is silently incomparable.
- Seam 5 (`workunits._shared`) is already satisfied — the whole family crosses as one
  `inbound` record on the picklable payload.

### 3. The selection artifact

The phase-1 → phase-2 hand-off, written as a post-analysis artifact in phase 1's run root,
shaped like the existing `run_channel_rollup` writer. `run_layout.json`'s `arms` key cannot
serve: it records the REQUESTED restock keys, is written once BEFORE simulation, and is
resume-guarded.

- Carries: the phase-1 run identity, the metric, **all seventeen rules in ranked order**
  (not just the cut — a later reader must see how close the decision was), the chosen five,
  the mandatory `fifo` rider, and which chosen rules need a gain-bundle extension.
- Per channel, since 08 ranks per channel and the two channels may carry different top-5s.
- Ranking is by total production hours summed across inventory profiles, which means this
  writer depends on [Build total production hours](19-build-total-production-hours.md).

## Comments

2026-08-31, from resolving "Design the phased funnel" (08): the mandatory `fifo` rider is
not a style choice. `run_channel_rollup._baseline_entry` prefers `uni_fifo`, falls back to
any key containing `fifo`, then falls back to `strategies[0]` — so a phase-2 arm set
without the `fifo` restock rule silently baselines against an arbitrary arm and renders
plausible, meaningless `saving_abs` for every row. Worth a test that a `fifo`-less arm set
fails loudly rather than rendering.
