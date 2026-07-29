"""runschema.v1 — the v1 run-tree contract: declarative level/artifact tables + the resolver.

This module is the SINGLE source of truth for what a v1 run directory looks like.  Two things
live here and nowhere else:

  * ``LEVELS``    — the ordered directory levels, each flagged optional + why.
  * ``ARTIFACTS`` — every file a run produces, as a path TEMPLATE plus its format/scope/schema.

``Optimization/schemas/run_tree.v1.json`` is GENERATED from these two tables (see
``runschema.contract``) and committed, so non-Python consumers (the JS viewer, notebooks) read the
same contract.  ``RunTreeV1`` is the Python face of it: downstream code asks it for paths instead of
joining strings, so a v2 tree means a new ``v2.py`` — not edits scattered across the analysis branch.

The walkers themselves stay in ``Optimization/runlayout.py`` (still the single owner of tree
traversal); this class delegates to them and adds the cell level, the optional-segment handling,
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

import os
from typing import Iterator

from Optimization import runlayout
from Optimization.runtime_metrics import RUNTIME_DB
from Optimization.sim_manifest import read_run_layout

SCHEMA_VERSION = 1

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
# path      : template relative to the run root.  `{name?}` = an OPTIONAL segment, dropped (with its
#             trailing separator) when that part is None.  `*` = a glob.
# scope     : which level the artifact belongs to — drives which resolver accessor returns it.
# writer    : the function that creates it, as `func@file` (kept in step with context/artifacts.yml).
ARTIFACTS = {
    # ── run root ────────────────────────────────────────────────────────────────
    'run_layout': {
        'path': 'run_layout.json', 'format': 'json', 'scope': 'run',
        'writer': 'write_run_layout@Optimization/sim_manifest.py',
        'note': 'the cell-tree descriptor; carries schema_version, which selects THIS contract.'},
    'run_spec': {
        'path': 'run_spec.json', 'format': 'json', 'scope': 'run',
        'writer': '_write_run_spec@Optimization/sim_manifest.py'},
    'run_log': {
        'path': 'run.log', 'format': 'text', 'scope': 'run',
        'writer': '_setup_logging@Optimization/sim_config.py'},
    'runtime_metrics_db': {
        'path': RUNTIME_DB, 'format': 'sqlite', 'scope': 'run', 'tables': ['runtime'],
        'writer': 'record_arm@Optimization/runtime_metrics.py',
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
        'path': 'whatif_delta.csv', 'format': 'csv', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only (a cross-cell delta needs >1 cell).',
        'writer': 'run@Optimization/run_whatif_delta.py'},
    'whatif_delta_json': {
        'path': 'whatif_delta.json', 'format': 'json', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_delta.py'},
    'whatif_delta_png': {
        'path': 'whatif_delta.png', 'format': 'png', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_delta.py'},
    'whatif_labor_csv': {
        'path': 'whatif_labor.csv', 'format': 'csv', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_labor.py'},
    'whatif_labor_json': {
        'path': 'whatif_labor.json', 'format': 'json', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_labor.py'},
    'whatif_labor_pngs': {
        'path': 'whatif_*.png', 'format': 'png', 'scope': 'run', 'optional': True,
        'condition': 'multi-cell runs only.',
        'writer': 'run@Optimization/run_whatif_labor.py'},

    # ── per cell ────────────────────────────────────────────────────────────────
    'run_manifest': {
        'path': '{cell}/run_manifest.json', 'format': 'json', 'scope': 'cell',
        'writer': 'write_run_manifest@Optimization/sim_manifest.py',
        'note': 'written PER CELL (_run_scenario receives the cell dir), not at the run root.'},
    'channel_rollup_csv': {
        'path': '{cell}/channel_rollup.csv', 'format': 'csv', 'scope': 'cell',
        'writer': 'rollup@Optimization/run_channel_rollup.py'},
    'channel_rollup_summary_csv': {
        'path': '{cell}/channel_rollup_summary.csv', 'format': 'csv', 'scope': 'cell',
        'writer': 'rollup@Optimization/run_channel_rollup.py'},
    # Cross-profile aggregate graphs + stats, one subtree per (config[, channel]) group.
    'aggregate_pngs': {
        'path': '{cell}/_aggregate/{config}/{channel?}/**/*.png', 'format': 'png', 'scope': 'cell',
        'writer': 'run_aggregate@Optimization/Performance_Evaluations/driver.py',
        'note': 'faceted/, overlay/, top/, breakdown/, stats_by_initial/<group>/.'},
    'aggregate_by_initial_csv': {
        'path': '{cell}/_aggregate/{config}/{channel?}/stats_by_initial/by_initial_summary.csv',
        'format': 'csv', 'scope': 'cell',
        'writer': 'run@Optimization/Performance_Evaluations/aggregate/stats_aggregate.py'},
    'aggregate_summary_csv': {
        'path': '{cell}/_aggregate/{config}/{channel?}/stats_by_initial/{initial_group}/'
                'aggregate_summary.csv',
        'format': 'csv', 'scope': 'cell',
        'writer': 'run@Optimization/Performance_Evaluations/aggregate/stats_aggregate.py'},
    'aggregate_tests_json': {
        'path': '{cell}/_aggregate/{config}/{channel?}/stats_by_initial/{initial_group}/'
                'aggregate_tests.json',
        'format': 'json', 'scope': 'cell',
        'writer': 'run@Optimization/Performance_Evaluations/aggregate/stats_aggregate.py'},

    # ── per pair (inside a cell) ────────────────────────────────────────────────
    'warehouse_db': {
        'path': '{cell}/{pair}/warehouse.db', 'format': 'sqlite', 'scope': 'pair',
        'tables': ['warehouse_stats', 'aisle_type_stats', 'aisle_layout'],
        'writer': 'build_shared_assets@Optimization/sim_assets.py'},
    'planned_inventory_db': {
        'path': '{cell}/{pair}/planned_inventory.db', 'format': 'sqlite', 'scope': 'pair',
        'optional': True,
        'condition': 'SINGLE-cell runs only — a multi-cell run shares _frozen/<pair>/'
                     'planned_inventory.db across cells.',
        'writer': 'build_shared_assets@Optimization/sim_assets.py'},
    'batches_cache': {
        'path': '{cell}/{pair}/_batches_*.pkl', 'format': 'pickle', 'scope': 'pair',
        'optional': True,
        'condition': 'the batch-precompute dedup; absent when a channel samples inline.',
        'writer': 'write_batches@Optimization/batch_precompute.py'},

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
        'writer': '_run_strategy_worker@Optimization/strategy_runner.py'},
    'keyframes_db': {
        'path': '{cell}/{pair}/{config}/{channel?}/sim_{strategy}.keyframes.db',
        'format': 'sqlite', 'scope': 'channel_run', 'tables': ['bin_keyframe'],
        'optional': True, 'condition': 'keyframe_interval > 0.',
        'writer': 'save_bin_keyframe@Optimization/Picking_Data.py'},
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
        'writer': 'run@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'stats_by_initial_pngs': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats_by_initial/{initial_group}/**/*.png',
        'format': 'png', 'scope': 'channel_run',
        'writer': 'run@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'batches_long_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/batches_long.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'run@Optimization/Performance_Evaluations/per_strategy/report_bars.py'},
    'per_run_summary_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/per_strategy/per_run_summary.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'run@Optimization/Performance_Evaluations/per_strategy/report_bars.py'},
    'summary_batch_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/per_strategy/summary_batch.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'run@Optimization/Performance_Evaluations/comparison/summary_csv.py'},
    'summary_task_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/per_strategy/summary_task.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'run@Optimization/Performance_Evaluations/comparison/summary_csv.py'},
    'stats_summary_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats/stats_summary.csv',
        'format': 'csv', 'scope': 'channel_run', 'optional': True,
        'condition': 'flat stats suite only (see stats_pngs).',
        'writer': 'run@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'stats_tests_json': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats/tests.json',
        'format': 'json', 'scope': 'channel_run', 'optional': True,
        'condition': 'flat stats suite only (see stats_pngs).',
        'writer': 'run@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'by_initial_summary_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats_by_initial/by_initial_summary.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'run@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'by_initial_stats_summary_csv': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats_by_initial/{initial_group}/'
                'stats_summary.csv',
        'format': 'csv', 'scope': 'channel_run',
        'writer': 'run@Optimization/Performance_Evaluations/stats/config_suite.py'},
    'by_initial_tests_json': {
        'path': '{cell}/{pair}/{config}/{channel?}/stats_by_initial/{initial_group}/tests.json',
        'format': 'json', 'scope': 'channel_run',
        'writer': 'run@Optimization/Performance_Evaluations/stats/config_suite.py'},

    # ── transient per-channel-run state (deleted when the config run finalizes) ──
    'resume_pkl': {
        'path': '{cell}/{pair}/{config}/{channel?}/resume.pkl',
        'format': 'pickle', 'scope': 'channel_run', 'optional': True,
        'condition': 'present only while a config run is in flight; removed on finalize.',
        'writer': '_save_resume@Optimization/sim_manifest.py'},
    'checkpoint_pkl': {
        'path': '{cell}/{pair}/{config}/{channel?}/_ckpt_{strategy}.pkl',
        'format': 'pickle', 'scope': 'channel_run', 'optional': True,
        'condition': 'present only while an arm is in flight; removed on finalize.',
        'writer': 'save_worker_checkpoint@Optimization/strategy_runner.py'},
}


# ── template rendering ──────────────────────────────────────────────────────────

def render(template: str, **parts) -> str:
    """Render an ARTIFACTS path template to a relative path.

    `{name}` substitutes parts[name]; `{name?}` is an OPTIONAL segment that is dropped along with
    its separator when parts.get(name) is None.  Raises KeyError for a missing required part, so a
    typo fails loudly instead of producing a path with a literal brace in it.
    """
    out = []
    for seg in template.split('/'):
        if seg.startswith('{') and seg.endswith('?}'):
            val = parts.get(seg[1:-2])
            if val is None:
                continue                      # optional segment absent (e.g. store-only channel)
            out.append(str(val))
            continue
        out.append(seg.format(**parts) if '{' in seg else seg)
    return '/'.join(out)


class RunTreeV1:
    """Resolver for a v1 run tree.  Construct via ``runschema.resolver_for(base_dir)``.

    Every accessor returns an ABSOLUTE path (or an iterator of them).  Existence is NOT implied —
    optional artifacts legitimately do not exist on some runs (see LEVELS/ARTIFACTS `condition`),
    so callers check `os.path.exists` where it matters.
    """

    version = SCHEMA_VERSION

    def __init__(self, base_dir: str, layout: dict | None = None):
        self.base = os.path.abspath(base_dir)
        self.layout = layout if layout is not None else (read_run_layout(self.base) or {})

    def __repr__(self) -> str:                                    # pragma: no cover - debug aid
        return f'<RunTreeV1 v{self.version} {os.path.basename(self.base)}>'

    # ── generic ────────────────────────────────────────────────────────────────
    def path(self, artifact: str, **parts) -> str:
        """Absolute path of any ARTIFACTS entry, e.g. path('sim_db', cell=…, pair=…,
        config=…, channel=None, strategy='uni_fifo_norsl')."""
        spec = ARTIFACTS[artifact]
        return os.path.join(self.base, render(spec['path'], **parts).replace('/', os.sep))

    @property
    def is_sweep(self) -> bool:
        """True when the run has >1 cell — the condition that produces _frozen/ and the
        cross-cell what-if outputs."""
        return len(self.layout.get('cells') or ()) > 1

    # ── run root ───────────────────────────────────────────────────────────────
    def run_layout_json(self) -> str:
        return self.path('run_layout')

    def run_spec_json(self) -> str:
        return self.path('run_spec')

    def runtime_db(self) -> str:
        return self.path('runtime_metrics_db')

    def whatif_outputs(self) -> list[str]:
        """Existing cross-cell what-if artifacts at the run root (empty on a single-cell run)."""
        names = ('whatif_delta_csv', 'whatif_delta_json', 'whatif_delta_png',
                 'whatif_labor_csv', 'whatif_labor_json')
        out = [self.path(n) for n in names]
        import glob as _glob
        out.extend(sorted(_glob.glob(self.path('whatif_labor_pngs'))))
        return [p for p in dict.fromkeys(out) if os.path.exists(p)]

    # ── cells ──────────────────────────────────────────────────────────────────
    def cells(self) -> list[tuple[str, str]]:
        """[(cell_name, cell_dir), …] in descriptor order (so partial/crashed cells are included)."""
        return list(runlayout.cells(self.base))

    def cell_dir(self, cell: str) -> str:
        return os.path.join(self.base, cell)

    def run_manifest(self, cell: str) -> str:
        return self.path('run_manifest', cell=cell)

    def rollup_csv(self, cell: str) -> str:
        return self.path('channel_rollup_csv', cell=cell)

    def aggregate_dir(self, cell: str, group_key: str) -> str:
        """<cell>/_aggregate/<group_key> — the cross-profile aggregate subtree.

        group_key is ChannelRun.group_key: `<config>` on a store-only run, `<config>/<channel>` on
        a mixed one, so the optional channel level is already folded in by the caller.
        """
        return os.path.join(self.base, cell, '_aggregate', *group_key.split('/'))

    # ── channel runs (the analysis leaf) ───────────────────────────────────────
    def channel_runs(self, cell: str | None = None) -> Iterator[tuple[str, runlayout.ChannelRun]]:
        """Yield (cell_name, ChannelRun) for every analyzed leaf.  `cell=None` spans the whole run.

        ChannelRun.channel is None on a store-only run — do NOT assume a channel segment.
        """
        items = [(cell, self.cell_dir(cell))] if cell is not None else self.cells()
        for name, cdir in items:
            for cr in runlayout.iter_channel_runs(cdir):
                yield name, cr

    def sim_dbs(self, cell: str | None = None) -> Iterator[tuple[str, runlayout.ChannelRun, str]]:
        """Yield (cell_name, ChannelRun, sim_db_path) across the run (or one cell).

        Structural — finds arms whose sim_meta.json was never finalized (crashed runs) too.
        """
        items = [(cell, self.cell_dir(cell))] if cell is not None else self.cells()
        for name, cdir in items:
            for cr, db in runlayout.iter_sim_dbs(cdir):
                yield name, cr, db

    @staticmethod
    def strategy_of(sim_db: str) -> str:
        """'…/sim_uni_fifo_norsl.db' -> 'uni_fifo_norsl'."""
        return os.path.basename(sim_db)[len('sim_'):-len('.db')]

    @staticmethod
    def keyframe_db(sim_db: str) -> str:
        return os.path.splitext(sim_db)[0] + '.keyframes.db'

    @staticmethod
    def sim_meta(cr: runlayout.ChannelRun) -> str:
        return os.path.join(cr.path, 'sim_meta.json')

    @staticmethod
    def series_json(cr: runlayout.ChannelRun) -> str:
        return os.path.join(cr.path, 'series.json')

    def config_json(self, cell: str, pair: str, config: str) -> str:
        return self.path('config_json', cell=cell, pair=pair, config=config)

    # ── pair-level assets ──────────────────────────────────────────────────────
    def warehouse_db(self, cell: str, pair: str) -> str:
        return self.path('warehouse_db', cell=cell, pair=pair)

    def planned_inventory_db(self, cell: str, pair: str) -> str:
        """The planned inventory for (cell, pair).

        Multi-cell runs freeze it once at _frozen/<pair>/ and share it across cells; single-cell
        runs write it under <cell>/<pair>/.  Prefer whichever exists, frozen first — that mirrors
        build_shared_assets, which returns the frozen DB verbatim when one was supplied.
        """
        frozen = self.path('frozen_inventory_db', pair=pair)
        if os.path.exists(frozen):
            return frozen
        return self.path('planned_inventory_db', cell=cell, pair=pair)

    # ── navigation axes (the viewer contract) ──────────────────────────────────
    def axes(self) -> dict[str, list[str]]:
        """Sorted distinct values per axis, observed from the tree and backfilled from the
        descriptor.  `channel` is [] on a store-only run — that emptiness is meaningful, and the
        UI should hide the channel selector rather than invent a value.
        """
        found: dict[str, set] = {a: set() for a in AXES}
        for cell, cr, db in self.sim_dbs():
            found['cell'].add(cell)
            found['pair'].add(cr.pair)
            found['config'].add(cr.config)
            if cr.channel:
                found['channel'].add(cr.channel)
            found['strategy'].add(self.strategy_of(db))
        # Descriptor backfill: a crashed/partial run may have cells with nothing on disk yet.
        for cell in (self.layout.get('cells') or ()):
            found['cell'].add(cell['name'])
        for pair in (self.layout.get('pairs') or ()):
            found['pair'].add(pair)
        return {a: sorted(found[a]) for a in AXES}
