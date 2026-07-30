"""runschema.schema — THE DECLARATION of the run tree.  Edit this file to change the tree.

This module is the single source of truth for what a run directory looks like.  Three things live
here and nowhere else:

  * ``FEATURES``  — the template vocabulary this declaration uses; a resolver must support all of it.
  * ``LEVELS``    — the ordered directory levels, each flagged optional + why.
  * ``ARTIFACTS`` — every file a run produces, as a path TEMPLATE plus its format/scope/schema.

There is deliberately NO version number here.  A schema's identity is the SHA-256 of these
declarations (``runschema.contract.schema_id``), so it is derived, never chosen: editing the tables
mints a new id automatically, two branches that make different edits get different ids instead of
colliding on "v2", and any consumer can re-derive the id and check it.  The generated document lands
at ``Optimization/schemas/run_tree/<short>.json`` and is committed, so non-Python consumers (the JS
viewer, notebooks) read the same contract.

Prose (``note`` / ``condition``) is EXCLUDED from the hash — documenting a level or artifact more
clearly must never mint a new schema.  Anything a path resolver actually depends on is included.

``runschema.resolver.RunTree`` interprets these tables generically; there is no per-schema Python.
The walkers themselves stay in ``Optimization/runschema/runlayout.py`` (still the single owner of tree
traversal); the resolver delegates to them and adds the cell level, the optional-segment handling,
and the axis inventory the viewer navigates by.

WHY LEVELS ARE CONDITIONAL — read before adding a level
-------------------------------------------------------
Two levels are NOT always present, and assuming otherwise is the bug this package exists to stop:

  * ``<channel>/`` appears only on a MIXED catalog (store + fulfillment).  A store-only run puts
    ``sim_*.db`` directly under ``<config>/``.  The pre-existing ``len(rel) < 4`` relpath checks in
    run_whatif_delta/run_whatif_labor silently dropped every store-only run for exactly this reason.
  * ``_frozen/<pair>/`` appears only on a MULTI-cell run, where the sampled inventory is frozen once
    and reshaped per cell.  A single-cell run samples fresh, so its ``planned_inventory.db`` sits at
    ``<cell>/<pair>/`` instead.

A second trap: the store *config* is named ``store`` and the store *channel* is also ``store``, so a
real path reads ``<cell>/<pair>/store/store/sim_*.db``.  Never infer a level by matching a directory
NAME — consume levels positionally against the axis lists in ``run_layout.json``.
"""
from __future__ import annotations

from Optimization.persistence.runtime_metrics import RUNTIME_DB

# ── template vocabulary ─────────────────────────────────────────────────────────
# What a resolver must be able to interpret to serve THIS declaration.  Replaces a version integer:
# resolver.py declares SUPPORTED_FEATURES, and resolver_for() refuses a contract naming a feature it
# doesn't implement — naming the missing one, which `schema_version > LATEST` never could.
#   optional-segments  `{name?}` — a path segment dropped, with its separator, when the part is None
#   globs              `*` (within a segment) and `**` (any depth) in a path template
#   resolves-via       an artifact that resolves to the first EXISTING of an ordered candidate list
#   strategy-capture   the `{strategy}` placeholder is invertible: a concrete path yields its arm
FEATURES = ('optional-segments', 'globs', 'resolves-via', 'strategy-capture')

# Run-root directory-name prefixes the driver produces (run_simulation._resolve_spec).
ROOT_PREFIXES = ('comparison_', 'comparison_whatif_')

# Directories directly under the run root (or a cell dir) that are NOT axis values.  The driver
# reserves a leading underscore for its own bookkeeping subtrees; every walker skips them.
RESERVED_PREFIX = '_'

# ── the ordered directory levels ────────────────────────────────────────────────
# `optional` levels are absent for the stated `condition`; downstream code must handle both shapes.
LEVELS = [
    {'name': 'cell',    'optional': False,
     'note': 'what-if cell: aisle-split x zoning x scheduler. Every run is a cell matrix; a plain '
             'run is the single cell k1_off.'},
    {'name': 'pair',    'optional': False,
     'note': 'inventory+affinity pair label (the warehouse) — <profile_run>__<profile>.'},
    {'name': 'config',  'optional': False,
     'note': 'pick-config name (store, store_high_height, ful_calibrated, ...).'},
    {'name': 'channel', 'optional': True,
     'condition': 'present only on a MIXED catalog (store + fulfillment); absent on a store-only '
                  'run, where sim_*.db sits directly under <config>/.'},
]

# The axes a run is navigated by.  `strategy` is not a directory level — it is the sim_<strategy>.db
# filename stem (the assignment-function arm) — but it IS a navigation axis for the viewer.
AXES = ('cell', 'pair', 'config', 'channel', 'strategy')

# ── every artifact a run produces ───────────────────────────────────────────────
# path        : template relative to the run root.  `{name?}` = an OPTIONAL segment, dropped (with
#               its trailing separator) when that part is None.  `*`/`**` = globs.
# scope       : which level the artifact belongs to — drives which resolver accessor returns it.
# writer      : the function that creates it, as `func@file` (kept in step with context/artifacts.yml).
# group       : optional tag; lets callers ask for a family of artifacts without hardcoding names.
# resolves_via: optional ordered candidate list — the artifact resolves to the first one that
#               EXISTS on disk.  Expresses a run-shape fallback as DATA instead of Python.
# HASHED: path, format, scope, optional, tables, resolves_via, group.  NOT hashed: writer, note,
# condition — attribution and prose must never mint a new schema id.
ARTIFACTS = {
    # ── run root ────────────────────────────────────────────────────────────────
    'run_layout': {
        'path': 'run_layout.json', 'format': 'json', 'scope': 'run',
        'writer': 'write_run_layout@Optimization/runschema/sim_manifest.py',
        'note': 'the cell-tree descriptor; carries schema_id, which selects THIS contract.'},
    'run_spec': {
        'path': 'run_spec.json', 'format': 'json', 'scope': 'run',
        'writer': '_write_run_spec@Optimization/runschema/sim_manifest.py'},
    'run_log': {
        'path': 'run.log', 'format': 'text', 'scope': 'run',
        'writer': '_setup_logging@Optimization/config/sim_config.py'},
    'runtime_metrics_db': {
        'path': RUNTIME_DB, 'format': 'sqlite', 'scope': 'run', 'tables': ['runtime'],
        'writer': 'record_arm@Optimization/persistence/runtime_metrics.py',
        'note': 'the ONLY DB carrying a `cell` column; sim_*.db knows its cell only by path.'},
    'runtime_pngs': {
        'path': '_runtime/*.png', 'format': 'png', 'scope': 'run',
        'writer': 'run@Optimization/run_runtime_graphs.py'},
    'frozen_inventory_db': {
        'path': '_frozen/{pair}/planned_inventory.db', 'format': 'sqlite', 'scope': 'run',
        'optional': True,
        'condition': 'MULTI-cell runs only — a single-cell run writes planned_inventory.db under '
                     '<cell>/<pair>/ instead.',
        'writer': '_run_whatif_matrix@Optimization/simdriver/scenario.py'},
    'frozen_warehouse_db': {
        'path': '_frozen/{pair}/warehouse.db', 'format': 'sqlite', 'scope': 'run',
        'optional': True, 'condition': 'MULTI-cell runs only (see frozen_inventory_db).',
        'writer': '_run_whatif_matrix@Optimization/simdriver/scenario.py'},
    'whatif_delta_csv': {
        'group': 'whatif',
        'path': 'whatif_delta.csv', 'format': 'csv', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only (a cross-cell delta needs >1 cell).',
        'writer': 'run@Optimization/run_whatif_delta.py'},
    'whatif_delta_json': {
        'group': 'whatif',
        'path': 'whatif_delta.json', 'format': 'json', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_delta.py'},
    'whatif_delta_png': {
        'group': 'whatif',
        'path': 'whatif_delta.png', 'format': 'png', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_delta.py'},
    'whatif_labor_csv': {
        'group': 'whatif',
        'path': 'whatif_labor.csv', 'format': 'csv', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_labor.py'},
    'whatif_labor_json': {
        'group': 'whatif',
        'path': 'whatif_labor.json', 'format': 'json', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_labor.py'},
    'analysis_log': {
        'path': 'analysis.log', 'format': 'text', 'scope': 'run', 'optional': True,
        'condition': 'standalone re-analysis only — analyze_run opens its own log file when run as '
                     'a CLI; the in-process path after a simulation reuses run.log, which is why '
                     'the preflight canaries never produce this.',
        'writer': 'main@Optimization/analyze_run.py'},
    'cell_analysis_log': {
        'path': '{cell}/analysis.log', 'format': 'text', 'scope': 'cell', 'optional': True,
        'condition': 'standalone re-analysis only (see analysis_log).',
        'writer': 'main@Optimization/run_analysis.py'},
    'whatif_volume_csv': {
        'group': 'whatif',
        'path': 'whatif_volume.csv', 'format': 'csv', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'note': 'per-arm cumulative-volume metrics (throughput, area gain, shape index); the '
                'numbers the docs pages cite. labor_hours here equals whatif_labor.csv exactly.',
        'writer': 'run@Optimization/run_whatif_volume.py'},
    'whatif_volume_json': {
        'group': 'whatif',
        'path': 'whatif_volume.json', 'format': 'json', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_volume.py'},
    'whatif_labor_pngs': {
        'group': 'whatif',
        'path': 'whatif_*.png', 'format': 'png', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only; the glob also covers whatif_volume_*.png.',
        'writer': 'run@Optimization/run_whatif_labor.py'},

    # ── per cell ────────────────────────────────────────────────────────────────
    'run_manifest': {
        'path': '{cell}/run_manifest.json', 'format': 'json', 'scope': 'cell',
        'writer': 'write_run_manifest@Optimization/runschema/sim_manifest.py',
        'note': 'written PER CELL (_run_scenario receives the cell dir), not at the run root.'},
    'channel_rollup_csv': {
        'path': '{cell}/channel_rollup.csv', 'format': 'csv', 'scope': 'cell',
        'writer': 'rollup@Optimization/run_channel_rollup.py'},
    'channel_rollup_summary_csv': {
        'path': '{cell}/channel_rollup_summary.csv', 'format': 'csv', 'scope': 'cell',
        'writer': 'rollup@Optimization/run_channel_rollup.py'},
    # Cross-profile aggregate graphs + stats, one subtree per (config[, channel]) group.
    'aggregate_dir': {
        'path': '{cell}/_aggregate/{config}/{channel?}', 'format': 'dir', 'scope': 'cell',
        'writer': '_aggregate_jobs@Optimization/run_analysis.py',
        'note': 'the group subtree; <config> alone on a store-only run, <config>/<channel> mixed.'},
    'aggregate_pngs': {
        'path': '{cell}/_aggregate/{config}/{channel?}/**/*.png', 'format': 'png', 'scope': 'cell',
        'writer': 'run_aggregate@Optimization/Performance_Evaluations/driver.py',
        'note': 'faceted/, overlay/, top/, breakdown/, stats_by_initial/<group>/.'},
    'aggregate_by_initial_csv': {
        'path': '{cell}/_aggregate/{config}/{channel?}/stats_by_initial/by_initial_summary.csv',
        'format': 'csv', 'scope': 'cell',
        'writer': 'render_stats_by_initial@Optimization/Performance_Evaluations/aggregate/stats_aggregate.py'},
    'aggregate_summary_csv': {
        'path': '{cell}/_aggregate/{config}/{channel?}/stats_by_initial/{initial_group}/'
                'aggregate_summary.csv',
        'format': 'csv', 'scope': 'cell',
        'writer': 'render_stats@Optimization/Performance_Evaluations/aggregate/stats_aggregate.py'},
    'aggregate_tests_json': {
        'path': '{cell}/_aggregate/{config}/{channel?}/stats_by_initial/{initial_group}/'
                'aggregate_tests.json',
        'format': 'json', 'scope': 'cell',
        'writer': 'render_stats@Optimization/Performance_Evaluations/aggregate/stats_aggregate.py'},

    # ── per pair (inside a cell) ────────────────────────────────────────────────
    'warehouse_db': {
        'path': '{cell}/{pair}/warehouse.db', 'format': 'sqlite', 'scope': 'pair',
        'tables': ['warehouse_stats', 'aisle_type_stats', 'aisle_layout'],
        'writer': 'build_shared_assets@Optimization/simdriver/sim_assets.py'},
    'planned_inventory_db': {
        'path': '{cell}/{pair}/planned_inventory.db', 'format': 'sqlite', 'scope': 'pair',
        'optional': True,
        'condition': 'SINGLE-cell runs only — a multi-cell run shares _frozen/<pair>/'
                     'planned_inventory.db across cells.',
        'writer': 'build_shared_assets@Optimization/simdriver/sim_assets.py'},
    # The planned inventory REGARDLESS of run shape: frozen first, because build_shared_assets
    # returns the frozen DB verbatim when one was supplied.  Declaring the precedence here is what
    # lets the resolver stay generic — it used to be an os.path.exists branch in Python.
    'planned_inventory': {
        'path': None, 'format': 'sqlite', 'scope': 'pair',
        'resolves_via': ['frozen_inventory_db', 'planned_inventory_db'],
        'note': 'alias: first candidate that exists on disk wins.'},
    'batches_cache': {
        'path': '{cell}/{pair}/_batches_*.pkl', 'format': 'pickle', 'scope': 'pair',
        'optional': True,
        'condition': 'the batch-precompute dedup; absent when a channel samples inline.',
        'writer': 'write_batches@Optimization/simdriver/batch_precompute.py'},

    # ── per config ──────────────────────────────────────────────────────────────
    'config_json': {
        'path': '{cell}/{pair}/{config}/config.json', 'format': 'json', 'scope': 'config',
        'writer': '_prepare_channel_run@Optimization/simdriver/workunits.py'},

    # ── per channel-run (the analysis leaf) ─────────────────────────────────────
    'sim_db': {
        'path': '{cell}/{pair}/{config}/{channel?}/sim_{strategy}.db',
        'format': 'sqlite', 'scope': 'channel_run',
        'tables': ['simulation_runs', 'batch_stats', 'task_stats', 'picker_events', 'picks',
                   'bin_inventory', 'aisle_metrics', 'reorder_queue', 'bin_scores', 'sku_scores'],
        'writer': '_run_strategy_worker@Optimization/simdriver/strategy_runner.py'},
    'keyframes_db': {
        'path': '{cell}/{pair}/{config}/{channel?}/sim_{strategy}.keyframes.db',
        'format': 'sqlite', 'scope': 'channel_run', 'tables': ['bin_keyframe'],
        'optional': True, 'condition': 'keyframe_interval > 0.',
        'writer': 'save_bin_keyframe@Optimization/persistence/Picking_Data.py'},
    'sim_meta': {
        'path': '{cell}/{pair}/{config}/{channel?}/sim_meta.json',
        'format': 'json', 'scope': 'channel_run',
        'writer': '_finalize_config_run@Optimization/simdriver/supervisor.py',
        'note': 'the completeness marker iter_channel_runs walks on.'},
    'series_json': {
        'path': '{cell}/{pair}/{config}/{channel?}/series.json',
        'format': 'json', 'scope': 'channel_run',
        'writer': '_dump_series@Optimization/Performance_Evaluations/common/series.py'},
    # ── analysis outputs at the channel-run leaf ────────────────────────────────
    # `**/*.png` spans the graph subdirs (compare/ nests faceted/, overlay/, top/, breakdown/).
    'per_strategy_pngs': {
        'path': '{cell}/{pair}/{config}/{channel?}/per_strategy/**/*.png',
        'format': 'png', 'scope': 'channel_run',
        'writer': 'prepare_config_dirs@Optimization/Performance_Evaluations/driver.py'},
    'compare_pngs': {
        'path': '{cell}/{pair}/{config}/{channel?}/compare/**/*.png',
        'format': 'png', 'scope': 'channel_run',
        'writer': 'prepare_config_dirs@Optimization/Performance_Evaluations/driver.py'},
    'stats_pngs': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats/**/*.png',
        'format': 'png', 'scope': 'channel_run', 'optional': True,
        'condition': 'presets that run the flat stats suite; BY_INITIAL (the default) writes '
                     'stats_by_initial/ instead.',
        'writer': 'render_suite@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'stats_by_initial_pngs': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats_by_initial/{initial_group}/**/*.png',
        'format': 'png', 'scope': 'channel_run',
        'writer': 'render_by_initial@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'batches_long_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/batches_long.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'render@Optimization/Performance_Evaluations/per_strategy/report_bars.py'},
    'per_run_summary_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/per_strategy/per_run_summary.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'render@Optimization/Performance_Evaluations/per_strategy/report_bars.py'},
    'summary_batch_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/per_strategy/summary_batch.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'render@Optimization/Performance_Evaluations/comparison/summary_csv.py'},
    'summary_task_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/per_strategy/summary_task.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'render@Optimization/Performance_Evaluations/comparison/summary_csv.py'},
    'stats_summary_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats/stats_summary.csv',
        'format': 'csv', 'scope': 'channel_run', 'optional': True,
        'condition': 'flat stats suite only (see stats_pngs).',
        'writer': 'render_suite@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'stats_tests_json': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats/tests.json',
        'format': 'json', 'scope': 'channel_run', 'optional': True,
        'condition': 'flat stats suite only (see stats_pngs).',
        'writer': 'render_suite@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'by_initial_summary_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats_by_initial/by_initial_summary.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'render_by_initial@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'by_initial_stats_summary_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats_by_initial/{initial_group}/'
                'stats_summary.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'render_by_initial@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'by_initial_tests_json': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats_by_initial/{initial_group}/tests.json',
        'format': 'json', 'scope': 'channel_run',
        'writer': 'render_by_initial@Optimization/Performance_Evaluations/stats/config_suite.py'},

    # ── transient per-channel-run state (deleted when the config run finalizes) ──
    'resume_pkl': {
        'path': '{cell}/{pair}/{config}/{channel?}/resume.pkl',
        'format': 'pickle', 'scope': 'channel_run', 'optional': True,
        'condition': 'present only while a config run is in flight; removed on finalize.',
        'writer': '_save_resume@Optimization/runschema/sim_manifest.py'},
    'checkpoint_pkl': {
        'path': '{cell}/{pair}/{config}/{channel?}/_ckpt_{strategy}.pkl',
        'format': 'pickle', 'scope': 'channel_run', 'optional': True,
        'condition': 'present only while an arm is in flight; removed on finalize.',
        'writer': 'save_worker_checkpoint@Optimization/simdriver/strategy_runner.py'},
}
