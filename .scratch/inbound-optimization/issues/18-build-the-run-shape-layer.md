# Build the run-shape layer

Type: task
Status: resolved
Blocked by: 08, 19

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

## Answer

BUILT — all three parts, on ONE run-tree schema event (`51f99901f03c` → `5c9bc35db55b`, the
single added artifact `restock_selection_json`). Every gate green; unit tier 1534.

### 1. The fifth `Cell` field

`Cell` is `(name, split, zoning, scheduler, inbound)`, the fifth **defaulted to `None`** so
every four-argument construction — in two test files and in whatever a resumed legacy run
recorded — keeps working. The value is a dict of `CONFIG['global']` `inbound_*` overrides with
the prefix dropped; `_apply_cell` writes them into `CONFIG['global']`, which is where they
belong: the yard and the dock are the SITE's, not a channel's, and that is exactly why the axis
had to become a cell field rather than six runs.

**The suffix goes BEFORE the scheduler suffix, not after** — `k{k}[_l{loss}]_{zone}[_{inbound}]
[_{sched}]`. `run_whatif_labor._scheduler_of` recovers the swept scheduler as the cell name's
LAST underscore token; a fifth segment appended after it would have relabelled every row of that
module's CSV `round_robin` — plausible, wrong, and about the one axis the name is parsed for.
The existing "one vocabulary" test gained the ordering as a pin.

**Three collapses refused, not merely documented.** The ticket named one; building it found two
more, and the third is the one that would have survived a review:

- an entry with **no name suffix** — the trap the ticket named: cells dedupe by name, so
  inbound-on and inbound-off become one directory with no error;
- a **duplicate** suffix — the same collapse by another route;
- an entry that **omits a key another entry sets**. `_apply_cell` mutates a process-wide CONFIG
  that is never reset between cells, so a key cell 3 writes and cell 4 omits leaves cell 4
  running cell 3's policy under its own name. Requiring every entry to cover the union of keys
  makes that carryover unreachable rather than unlikely. It is also why the phase-2 spec carries
  the whole arrival regime (lead median, spread, doors) and not just the policies: the
  inbound-OFF anchor must turn the spread off too, because `inbound_spec()` refuses a spread
  without the standing yard and refuses it ABOVE the trailer-type early return — an anchor
  inheriting a run-level `--inbound-lead-spread` would raise instead of running.

Plus a fourth, cheap: an unknown key is refused, because a misspelt one would create a new
CONFIG entry `inbound_spec()` never reads and the cell would run the DEFAULT policy under the
swept policy's name.

**`is_reference` gained `inbound is None`**, and a swept axis must DECLARE `reference`. Without
the clause, all ten phase-2 cells would answer True and `reference_cell` would take whichever
was built first — the same silent-arbitrary-baseline failure `_baseline_entry` was caught doing,
one level up.

**Two specs, not one.** `inbound_select` is phase 1 (one cell, inbound off, `lpt`, all arms);
`inbound_policies` is the ten-cell matrix, built by `phase2_inbound_axis()` from six named
constants. The H points are MULTIPLES of the threshold rather than absolute days, and the
builder writes the threshold into every cell's own record, so the two cannot drift apart when
the pilot calibrates it. `PHASE2_ARMS` is `None` and **`_run_whatif_matrix` refuses an inbound
matrix that has no arm set** rather than falling through to the committed full suite — that
fallthrough is 34 arms × 10 cells where the funnel budgeted 12, and it is not the experiment.

08's comment about the `fifo` rider is paid at **two** levels: the matrix refuses an explicit
arm subset without `fifo` at minute zero, and `run_channel_rollup._baseline_entry` no longer
falls back to `strategies[0]` at all — it raises. That fallback was silent AND its output was
plausible: every `saving_abs` measured against an arbitrary arm, labelled `baseline_fifo_ss`.

### 2. Seams 3–4 for the whole family

**One list, `sim_config.INBOUND_KEYS`, and everything derives from it** — the flags, the run-spec
record, the resume whitelist, the re-analysis restore. That is the point: the failure mode of
seam 4 is the HALF-fix (recorded but not restored, or restored in one of the two sites), and a
hand-maintained second copy is how the half-fix happens. A test pins the list against the
`inbound_*` keys actually in CONFIG.

Seventeen flags, one per key — more than the ticket's minimum, because a knob recorded in the
spec but reachable only by editing `settings.py` cannot be restored onto `args` on a resume, so
seams 3 and 4 have to line up 1:1. Every flag **defaults FROM CONFIG**, and that is load-bearing
rather than tidy: `main` assigns the whole family unconditionally, so a flag defaulting to a
literal would silently overwrite `settings.py` on every flag-less run.

`--inbound-futuresight-batches` needed a converting `type=`: `_futuresight_batches` rejects
`'5'` as firmly as `'oracle'` (its `w != raw` test stops fractions, and a numeric string fails
it too), so without one the flag would parse cleanly and then refuse three layers down, naming
the settings constant rather than the flag the user typed.

The lead TAG (`0x1EAD`) is recorded, **imported from `Inbound.transit` rather than restated** —
a second copy of a literal whose whole job is to be stable is a copy that can drift. It is
provenance, not run shape: it is deliberately NOT in the restore whitelist.

**One thing the ticket did not name, found while wiring it.** 07's derive-late fee report reads
`ctx.fee_threshold_days()`, which reads `sim_result` — and `_sim_result_from_meta` copies five
keys from `sim_meta.json`, so the recorded threshold could never have reached it and the
fallback fired on EVERY run. CONFIG cannot be the channel either: an analysis worker is SPAWNED
and re-imports `sim_config` with pristine defaults. So `_apply_run_shape` restores it in the
parent and `_sim_result_from_meta` stamps it onto the pickled job. Without this, recording the
threshold would have been recorded and then ignored — seam 4's exact failure, one layer further
down than the seam.

A pre-field spec restores the family to all-None, which `inbound_spec()` reads as OFF and every
downstream `or` default turns back into the value the run used. That is the `sampler` reasoning
applied to a whole family: absence means "that run had no inbound pipeline", never this
checkout's settings.

### 3. The selection artifact

`Optimization/run_restock_selection.py` → `<phase-1 run root>/restock_selection.json`.

- **Unit is a RULE**, resolved through `STRATEGY_BY_KEY` rather than by parsing arm keys —
  `uni_rank_labor_norsl` splits into three fields whose middle one contains underscores, so
  every hand-rolled parse is a guess. Unknown keys are dropped and NAMED.
- **Score**: `ss_prod_total` summed over the channel's (profile, config) leaves, reported in
  hours. Every rule is measured on the identical leaf set, so the sum is like-for-like; a MIN
  over leaves would score two rules on different profiles. A RULE's score is the minimum over
  its own ARMS (selecting it takes both, and what earns it a place is that one performs), with
  both arm totals recorded so a reader sees when they disagree.
- **Absence disqualifies.** A rule with any missing or non-finite reading is excluded, named in
  the log, and carries `rank: null` — a partial sum would rank a rule measured on fewer profiles
  as cheapest, which is the most plausible wrong answer this module can produce.
- All 17 ranked, per channel, plus the chosen k, the `fifo` rider, the extension cap's
  backfill trail, and the metric's own provenance — including WHY the cross-profile CSV is not
  the source (`_aggregate_series` normalizes to each profile's baseline, so it holds ratios).
- The faithful-bundle set moved to `Inbound.gain.FAITHFUL_GAIN_FAMILIES`, so the selector reads
  it without importing the simulation and the driver's refusal message names the same list;
  a test pins the constant against the branches `_gain_bundle_for` actually has.
- The output path resolves through `runschema.analysis_path` (HEAD-first), not `rt.path`: a
  phase-1 run simulated under an older contract has never heard of this artifact, and `rt.path`
  would raise `KeyError` for a location that is perfectly legal to write.
- The arm set is copied into `PHASE2_ARMS` **by hand, deliberately**. A spec that read a JSON
  file at import would make the decision invisible.

### One blocker surfaced, routed rather than fixed

Building the selector's "which chosen rules need an extension" field exposed that **phase 2
cannot run as 08 specified it**. A gain cell builds `_gain_bundle_for(strat, …)` for EVERY arm
in the set — the gate is on the POLICY, not the arm — and `fifo` is a MANDATORY rider with no
faithful bundle. All five gain cells would refuse both fifo arms at worker startup. Verified by
calling the function, not inferred, and pinned.

It belongs to [Extend the gain bundles](20-extend-the-gain-bundles.md), which the finding
changes twice: the rider sits OUTSIDE 08's cap of three (the cap is about which OPTIONAL
families are worth extending), and that piece is NOT gated on phase 1 — `fifo` is in every
possible arm set, so it can be done first and de-risks the 480-unit sweep. The selector reports
it in its own field, `rider_needs_bundle_extension`, and logs it loudly.

### Residue, named

- `_scheduler_of` still mislabels a single-scheduler run whose one scheduler is not
  `round_robin` — `inbound_select` and `inbound_policies` both fix `lpt`, so both will read
  `round_robin` in `whatif_labor`'s CSV column. PRE-EXISTING (any single-`lpt` spec did this
  before this ticket) and not made worse: the suffix ordering above means no new instance. The
  fix is reading the scheduler off `run_layout.json`, which the existing test already flags as
  needing its own evidence.
- The lead TAG is recorded but nothing REFUSES a resume across a TAG change; `repo_commit`
  already records the checkout, so the evidence exists without a guard for it.

## Comments

2026-08-31, from resolving [Build total production hours](19-build-total-production-hours.md):
the `Blocked by:` line above gained 19. It was always there in the body — part 3 says this
writer "depends on" it — but never wired, so this ticket sat on the frontier ahead of the
ticket it needs.

Three things 19 settled that change part 3:

- **The ranking metric now exists**, and is `total_production_time` (unload + put + pick),
  reachable per batch, per arm, in the significance CSVs and as `ss_prod_total` in every
  profile's series document.
- **Do NOT rank off the cross-profile summary CSV.** `total_production_time` is
  deliberately absent from `AGGREGATE_ORDER`, and the decisive reason is not the pinned
  oracle: `_aggregate_series` NORMALIZES each profile to its own baseline, so that CSV
  holds RATIOS. 08 ranks on hours SUMMED ACROSS PROFILES, so this writer sums
  `ss_prod_total` over the profiles' series documents itself.
- **Absence must be loud.** A run without `work_events` produces no
  `total_production_time` row at all — correctly, since the metric is capability-gated —
  and a selector that reads a missing row as a tie would rank all seventeen rules on
  nothing. Phase 1 is a fresh run so it will have the rows; the guard is for the re-run.

Part 2 is unaffected, except that `fee_threshold_days`' fallback notice now fires only on
runs that actually have trailer rows (19 stopped `yard_frame` consulting the threshold when
there are none), so the log line this ticket must make unnecessary is no longer buried under
one per inbound-off arm.

2026-08-31, from resolving "Design the phased funnel" (08): the mandatory `fifo` rider is
not a style choice. `run_channel_rollup._baseline_entry` prefers `uni_fifo`, falls back to
any key containing `fifo`, then falls back to `strategies[0]` — so a phase-2 arm set
without the `fifo` restock rule silently baselines against an arbitrary arm and renders
plausible, meaningless `saving_abs` for every row. Worth a test that a `fifo`-less arm set
fails loudly rather than rendering.
