"""EvalContext / AggregateContext — the per-config (and per-aggregate-group) data hub.

Built once by the driver and passed to every graph's render(ctx, params).  Lazily loads
and caches exactly what each graph touches (batch/task frames per strategy, the series
dict, the travel/handling breakdown, steady-state cutoffs), so the duplicated per-graph
reloading of the old monolith is gone.  A graph never reloads a DB the context already
holds; it just calls ctx.batch_df(key) / ctx.series() / ctx.breakdown().
"""
from __future__ import annotations

import logging
import os

from Schema import compat as _compat
from Schema import identity as _identity

from Optimization.Performance_Evaluations.core import requests as _requests
from Optimization.Performance_Evaluations.common.series import _aggregate_series
from Optimization.Performance_Evaluations.common.style import _focus_filter, _WIN


def _provenance_parts(leaf_dir: str, max_up: int = 6) -> list[str]:
    """Directory names from the RUN ROOT down to `leaf_dir`, for a figure's provenance footer.

    The run root is the dir holding run_layout.json (the run-tree descriptor).  Anchoring on that
    marker keeps the label correct whether the leaf is `<cell>/<pair>/<config>` (store-only) or
    `<cell>/<pair>/<config>/<channel>` (mixed) — a fixed dirname-hop count cannot be right for both.
    Falls back to the last three components when no descriptor is found (e.g. an ad-hoc dir under
    test), so this never raises inside a plotting call.
    """
    leaf = os.path.abspath(leaf_dir)
    parts: list[str] = []
    d = leaf
    for _ in range(max_up):
        if os.path.exists(os.path.join(d, 'run_layout.json')):
            return [os.path.basename(d)] + parts
        parent = os.path.dirname(d)
        if parent == d:
            break
        parts.insert(0, os.path.basename(d))
        d = parent
    return leaf.replace('\\', '/').split('/')[-3:]


# ── schema identity: the gate between an archived file and a published number ────────────────
# THE highest-value check in the repo, and the reason `Schema/` exists.  Every table this
# context reaches is loaded by a `Picking_Data` function that does `SELECT *` and guards each
# field with `row.keys()`, so a dropped or renamed column does not raise — it arrives as the
# dataclass default in `common/frames.py` (`BatchStats.thr_task = 0.0`, `task_makespan = 0.0`,
# …), flows into `series()`, and is rendered as a figure that is quietly, plausibly wrong.
#
# HARD FAIL, not a warning, and deliberately so.  This path PUBLISHES; the output outlives the
# session that produced it and carries no signal that a column was missing.  A run whose sim DBs
# are not vetted must stop the analysis, name the columns, and let a human decide — which is
# exactly what `UnsupportedSchema`'s structural diff is for.  The archive's live vintages
# (2026-07-29 -> `23d0c7f167bc`, 2026-08-13 -> `ee5ebabe74fb`) are both in `SIM_DB_FAMILY`'s
# `known_ids`; the older cold-archive shapes are not, and that is the point — see
# `Picking_Data.UNVETTED_ARCHIVE_SIM_SCHEMA_IDS`.

# What this context reads, at the granularity a consumer actually reads it.  `_verify_sim_dbs`
# below asks whether the FILE is vetted; this asks whether it can answer THESE questions — and a
# file can be vetted and still lack a column a particular caller needs, which is exactly what
# `known_ids` permits.  Every entry is inside `Schema.compat.guaranteed_surface('sim_db')`, so this
# context is version-free across all four vetted vintages by construction rather than by accident;
# `Tests/architecture/test_schema_compatibility.py` fails if a schema change breaks that.
#
# Only three tables, because ALL of this package's DB access is the three Picking_Data loaders
# the request broker (core/requests.py) calls — no chart-family module (headline/, labor/,
# significance/, aggregate/, …) opens a database itself.  Keep it that way: a graph that opens
# its own connection escapes this check AND the broker's access log.
REQUIRES = _compat.Requires(
    family='sim_db',
    label='Performance_Evaluations analysis context',
    tables={
        # `sigma_fd`, `reload_moves`, `reorder_placements`, `queue_depth`, `lead_queue_depth` and
        # `in_transit_qty` are GUARDED in the loader and published anyway (the diagnostics
        # scorecards, the tables evals, stats_core._METRICS), so losing one yields a plausible
        # zero on a figure rather than an error.  They are declared for exactly that reason.
        'batch_stats': ('run_id', 'batch_id', 'duration', 'num_tasks', 'total_items',
                        'avg_concurrent_pickers', 'picking_pct', 'traveling_pct', 'is_outlier',
                        'task_makespan', 'sigma_fd', 'reload_moves', 'reorder_placements',
                        'queue_depth', 'lead_queue_depth', 'in_transit_qty'),
        # `W` is the objective_task_labor / objective_total_labor metric, published through
        # the task frame and the tidy tables — guarded to 0.0.
        'task_stats': ('run_id', 'batch_id', 'aisle_id', 'picker_id', 'task_start_time',
                       'task_end_time', 'duration', 'lift_sum', 'num_bins_visited', 'total_items',
                       'is_outlier', 'W'),
        # `task_time_breakdown` needs only picker_id/time/event_type, but the loader materializes
        # every column before the breakdown sees a row.
        'picker_events': ('run_id', 'batch_id', 'picker_id', 'time', 'event_type', 'aisle_id',
                          'bayX', 'bayY', 'sku', 'quantity', 'bins_completed', 'total_bins',
                          'items_picked', 'total_items'),
    })


def _verify_sim_dbs(strategies, log: logging.Logger) -> None:
    """Refuse to build a context over an unvetted sim DB.  Raises `UnsupportedSchema`.

    One check per DISTINCT file, ~10 PRAGMA round trips each.  Measured at 28 ms per sim DB on
    the external results drive, so ~1 s for a 34-arm config — small beside the `batch_stats`
    load it guards, and paid once per context because `run_analysis` memoizes contexts per
    (config, focus) in `_CFG_CTX`.  Not worth a cache of its own.
    """
    for path in dict.fromkeys(s['db_path'] for s in strategies if s.get('db_path')):
        if not os.path.exists(path):
            continue                    # absent is the loaders' business, not identity's
        sid = _identity.check(path, 'sim_db', verify=True)
        log.debug(f'  schema ok: {os.path.basename(path)} = {sid}')


class EvalContext:
    """Per-config context.  Build via EvalContext.from_job (driver) or the __init__."""

    def __init__(self, sim_result: dict, slim: dict, focus: str,
                 log: logging.Logger) -> None:
        self.sim_result = sim_result
        self.run_dir    = sim_result['run_dir']
        self.name       = sim_result['name']
        self.inv        = sim_result.get('inventory', '') or self.name
        self.optimal      = float(sim_result.get('optimal_sigma_fd') or 0.0)
        self.optimal_work = float(sim_result.get('optimal_work') or 0.0)
        self.aisle_unittype_map = slim['aisle_unittype_map']
        self.aisle_handling_map = slim['aisle_handling_map']
        self.k_pickers  = slim.get('k_pickers', 25)
        self.total_bins = float(slim.get('total_bins') or 0)
        self.log        = log

        # focus filter applied ONCE here, so every graph sees the same strategy set.
        # (BY_INITIAL preset uses focus='all' → no-op filter → both families present.)
        self.strategies = _focus_filter(sim_result['strategies'], focus)
        # Before ANY of those files is read as numbers.  See `_verify_sim_dbs`.
        _verify_sim_dbs(self.strategies, log)
        self.base       = self.strategies[0]
        self._by_key    = {s['key']: s for s in self.strategies}

        self._bcache: dict = {}
        self._tcache: dict = {}
        self._series = None
        self._breakdown = None
        self._maxb = None

    @classmethod
    def from_job(cls, job: dict, focus: str) -> 'EvalContext':
        log = logging.getLogger('analysis')
        return cls(job['sim_result'], job['slim'], focus, log)

    @property
    def title(self) -> str:
        return f'{self.inv} / {self.name}'

    def footer(self) -> str:
        """Provenance line stamped on every figure: origin sim folder + inventory.

        run_dir is `<run>/<cell>/<pair>/<config>` on a store-only run and
        `<run>/<cell>/<pair>/<config>/<channel>` on a mixed one, so a FIXED number of dirname hops
        mislabels one of the two — the old three-hop version rendered the config name in the "run"
        slot for every channelized run.  Walk up to the run root (the dir holding run_layout.json)
        instead, so the label is right at either depth.
        """
        parts = _provenance_parts(self.run_dir)
        return f"sim: {' / '.join(parts)}     inventory: {self.inv}"

    def full_title(self, s) -> str:
        bits = [self.inv, s.get('initial', ''), s.get('assignment', ''), s.get('reslot', '')]
        return '_'.join(b for b in bits if b)

    # ── lazy per-strategy frames — the BROKER FACADE ──────────────────────────
    # Same signatures, same return shapes, same memoisation as always; the bodies live in
    # core/requests.py, the intermediary that owns navigation from versioned sources to
    # composed resources.  Graph modules keep calling these and never learn the difference.
    def batch_df(self, key):
        return _requests.batch_frame(self, key)

    def task_df(self, key):
        return _requests.task_frame(self, key)

    def batch_frames(self) -> dict:
        return {s['key']: self.batch_df(s['key']) for s in self.strategies}

    def task_frames(self) -> dict:
        return {s['key']: self.task_df(s['key']) for s in self.strategies}

    # ── memoized derived products (broker facade, continued) ──────────────────
    def series(self) -> dict:
        return _requests.series_dict(self)

    def maxb(self) -> int:
        if self._maxb is None:
            self._maxb = max((int(self.batch_df(s['key'])['batch_id'].max())
                              for s in self.strategies
                              if not self.batch_df(s['key']).empty), default=0)
        return self._maxb

    def ss_lo(self) -> int:
        return self.maxb() - _WIN + 1

    def breakdown(self) -> dict:
        """{key: (travel, handling)} from picker_events over a steady-state sample.
        Memoized; returns {} (logged) on failure so dependent graphs degrade gracefully."""
        return _requests.breakdown_dict(self)


class AggregateContext:
    """Cross-profile context for one pick-config group (consumes the series.json list)."""

    def __init__(self, profile_series_list: list, out_dir: str, pickcfg: str,
                 focus: str, log: logging.Logger) -> None:
        self.out_dir = out_dir
        self.pickcfg = pickcfg
        self.log     = log
        self.n_profiles = len(profile_series_list)
        # Focus filter (default uni) per profile so plots + stats agree; focus='all'
        # (the by-initial preset) keeps BOTH families, as compare='initial' did.
        if focus in ('uni', 'opt'):
            filtered = []
            for ps in profile_series_list:
                subs = [d for d in ps.get('strategies', [])
                        if str(d.get('key', '')).startswith(focus + '_')]
                filtered.append({**ps, 'strategies': subs or ps.get('strategies', [])})
            profile_series_list = filtered
        self.profile_series_list = profile_series_list
        self._agg = None

    @classmethod
    def from_job(cls, job: dict, focus: str) -> 'AggregateContext':
        log = logging.getLogger('analysis')
        return cls(job['profile_series_list'], job['out_dir'], job['pickcfg'], focus, log)

    def footer(self) -> str:
        # out_dir is <run>/<cell>/_aggregate/<config>[/<channel>] — same variable depth as the
        # per-config footer, so anchor on the run-root marker rather than counting dirname hops.
        parts = _provenance_parts(self.out_dir)
        return (f"sim: {' / '.join(parts)}     "
                f"{self.n_profiles} profiles (cross-profile)")

    def agg_series(self):
        if self._agg is None:
            self._agg = _aggregate_series(self.profile_series_list)
        return self._agg


class RunContext:
    """Whole-run context: the run ROOT and everything under it, across cells.

    The third scope, and the one the other two structurally cannot be.  `EvalContext` sees
    one channel-run leaf; `AggregateContext` sees the profiles of one cell x config x
    channel.  Neither can see two cells, so every cross-cell question — the scheduler
    comparison, the compute cost of a rule across the whole sweep, what the run held fixed
    — had to live outside the registry, in hand-written writers whose figures the family
    grammar never checked and whose PNGs the site guard needs a hardcoded exemption for.
    This is the scope those belong in.

    Reads are memoised and lazy, in the same spirit as `EvalContext`: nothing is opened
    until an evaluation's `needs=` resolves it through the broker.
    """

    def __init__(self, run_root: str, out_dir: str, log: logging.Logger) -> None:
        self.run_root = run_root
        self.out_dir = out_dir                  # the dossier root; io.out_dir's base
        self.log = log
        self._rt = None
        self._runtime = None
        self._spec = None
        self._whatif: dict = {}

    @classmethod
    def from_run_root(cls, run_root: str, out_dir: str) -> 'RunContext':
        return cls(run_root, out_dir, logging.getLogger('analysis'))

    @property
    def rt(self):
        """The run-tree resolver for this run — the ONLY way to reach a path under it.

        Bound to the contract the RUN recorded, which is right for everything the
        simulation wrote.  Artifacts the ANALYSIS writes go through `reader()` instead.
        """
        if self._rt is None:
            from Optimization.runschema import resolver_for
            self._rt = resolver_for(self.run_root)
        return self._rt

    def reader(self, artifact: str):
        """A resolver for an analysis-produced artifact — HEAD's contract, then the run's.

        Run-scope evaluations read what the SAME analysis pass just wrote, so they must
        look where today's suite puts things.  `runschema.reader_for` carries the whole
        argument, including why an absence-only fallback silently loses a moved file.
        """
        from Optimization.runschema import reader_for
        return reader_for(self.rt, artifact)

    def footer(self) -> str:
        return (f'sim: {os.path.basename(os.path.abspath(self.run_root))}     '
                f'whole run (all cells)')

    def run_spec(self) -> dict:
        """The run's own recorded invocation spec, or {} when it has none."""
        if self._spec is None:
            import json
            self._spec = {}
            try:
                path = self.rt.path('run_spec')
            except Exception:                              # noqa: BLE001 - absence is data
                return self._spec
            if os.path.exists(path):
                with open(path, encoding='utf-8') as fh:
                    self._spec = json.load(fh)
        return self._spec

    def run_workers(self):
        """How many worker processes the sweep ran, or None if unrecorded.

        Load-bearing provenance for anything quoting a wall time: these seconds were
        measured under that much contention, so they are an upper bound rather than a
        clean benchmark, and a ratio against an uncontended measurement is not a ratio.
        """
        w = self.run_spec().get('workers')
        return int(w) if w else None

    def runtime_rows(self) -> list:
        """Per-arm compute-cost rows, or [] when the run predates the runtime DB.

        These are REAL wall seconds, contended against whatever worker pool the sweep
        ran — never the modeled warehouse seconds every other context serves.  Anything
        rendering them says which kind of second it means.
        """
        if self._runtime is None:
            from Optimization.persistence.runtime_metrics import load_rows
            self._runtime = load_rows(self.run_root)
        return self._runtime

    def whatif_doc(self, artifact: str) -> dict:
        """A cross-cell what-if JSON summary, resolved through the contract ({} if absent)."""
        if artifact not in self._whatif:
            import json
            try:
                path = self.rt.path(artifact)
            except Exception:                              # noqa: BLE001 - absence is data
                self._whatif[artifact] = {}
                return self._whatif[artifact]
            if not os.path.exists(path):
                self._whatif[artifact] = {}
            else:
                with open(path, encoding='utf-8') as fh:
                    self._whatif[artifact] = json.load(fh)
        return self._whatif[artifact]

    def whatif_rows(self, artifact: str) -> list:
        """A cross-cell what-if CSV as a list of dicts, resolved through the contract."""
        if artifact not in self._whatif:
            import csv
            try:
                path = self.rt.path(artifact)
            except Exception as exc:                       # noqa: BLE001 - absence is data
                self.log.info(f'  whatif {artifact}: unresolvable ({exc!r})')
                self._whatif[artifact] = []
                return self._whatif[artifact]
            if not os.path.exists(path):
                self._whatif[artifact] = []
            else:
                with open(path, newline='', encoding='utf-8') as fh:
                    self._whatif[artifact] = list(csv.DictReader(fh))
        return self._whatif[artifact]

    def catalogue_dbs(self) -> list:
        """[(pair, planned_inventory_db_path)] for the catalogues this run actually stocked.

        `planned_inventory` is a contract ALIAS: a multi-cell run freezes one catalogue per
        pair under the run root, a single-cell run writes one per cell.  Both `cell` and
        `pair` are supplied so the alias can resolve either shape — which is the whole
        reason to go through the resolver instead of joining the frozen pair directory.
        """
        out, seen = [], set()
        for cell, cr in self.rt.channel_runs():
            if cr.pair in seen:
                continue
            try:
                path = self.rt.path('planned_inventory', cell=cell, pair=cr.pair)
            except Exception:                              # noqa: BLE001 - absence is data
                continue
            if os.path.exists(path):
                seen.add(cr.pair)
                out.append((cr.pair, path))
        return out

    def leaf_rows(self, artifact: str) -> list:
        """Every channel-run leaf's copy of a per-leaf CSV, each row tagged with its leaf.

        The tag is what makes a run-scope reduction possible at all: the per-leaf tables
        carry no cell/pair/config/channel columns, because at leaf scope those are the
        directory.  Positional resolution through the resolver, never a name match — the
        store CONFIG and the store CHANNEL are both called `store`.
        """
        import csv
        rd = self.reader(artifact)
        if rd is None:
            self.log.info(f'  leaf {artifact}: declared by no contract this build knows')
            return []
        out = []
        for cell, cr in self.rt.channel_runs():
            try:
                path = rd.leaf_path(cr, artifact)
            except Exception as exc:                       # noqa: BLE001 - absence is data
                self.log.info(f'  leaf {artifact}: unresolvable ({exc!r})')
                return out
            if not os.path.exists(path):
                continue
            tag = {f'_{k}': v for k, v in self.rt.parts_of(cell, cr).items()}
            with open(path, newline='', encoding='utf-8') as fh:
                out.extend({**row, **tag} for row in csv.DictReader(fh))
        return out
