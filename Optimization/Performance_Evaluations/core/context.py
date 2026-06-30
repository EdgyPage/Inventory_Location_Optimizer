"""EvalContext / AggregateContext — the per-config (and per-aggregate-group) data hub.

Built once by the driver and passed to every graph's render(ctx, params).  Lazily loads
and caches exactly what each graph touches (batch/task frames per strategy, the series
dict, the travel/handling breakdown, steady-state cutoffs), so the duplicated per-graph
reloading of the old monolith is gone.  A graph never reloads a DB the context already
holds; it just calls ctx.batch_df(key) / ctx.series() / ctx.breakdown().
"""
from __future__ import annotations

import logging

import numpy as np

from Picking_Data import load_batch_stats, load_task_stats, load_picker_events
from Simulation_Analytics import task_time_breakdown

from Performance_Evaluations.common.frames import _bdf, _tdf
from Performance_Evaluations.common.series import _build_series, _aggregate_series
from Performance_Evaluations.common.style import _focus_filter, _WIN


def _strategy_travel_handling(strategies, ss_lo, max_b, n_sample=8):
    """Per-strategy (travel, handling) picker-time totals over a sample of steady-state
    batches, reconstructed from picker_events.  Returns {key: (travel, handling)}."""
    lo, hi = max(0, ss_lo), max_b
    if hi < lo:
        return {}
    batch_ids = sorted({int(round(x)) for x in np.linspace(lo, hi, min(n_sample, hi - lo + 1))})
    out = {}
    for s in strategies:
        tr = hd = 0.0
        for b in batch_ids:
            t, h, _ = task_time_breakdown(load_picker_events(s['db_path'], s['run_id'], b))
            tr += t; hd += h
        out[s['key']] = (tr, hd)
    return out


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

    def full_title(self, s) -> str:
        bits = [self.inv, s.get('initial', ''), s.get('assignment', ''), s.get('reslot', '')]
        return '_'.join(b for b in bits if b)

    # ── lazy per-strategy frames ──────────────────────────────────────────────
    def batch_df(self, key):
        df = self._bcache.get(key)
        if df is None:
            s = self._by_key[key]
            df = _bdf(load_batch_stats(s['db_path'], s['run_id']))
            self._bcache[key] = df
        return df

    def task_df(self, key):
        df = self._tcache.get(key)
        if df is None:
            s = self._by_key[key]
            df = _tdf(load_task_stats(s['db_path'], s['run_id']),
                      self.aisle_unittype_map, self.aisle_handling_map)
            self._tcache[key] = df
        return df

    def batch_frames(self) -> dict:
        return {s['key']: self.batch_df(s['key']) for s in self.strategies}

    def task_frames(self) -> dict:
        return {s['key']: self.task_df(s['key']) for s in self.strategies}

    # ── memoized derived products ─────────────────────────────────────────────
    def series(self) -> dict:
        if self._series is None:
            self._series = _build_series(self.strategies, self.batch_frames(),
                                         self.task_frames())
        return self._series

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
        if self._breakdown is None:
            try:
                self._breakdown = _strategy_travel_handling(self.strategies,
                                                            self.ss_lo(), self.maxb())
            except Exception as exc:                                    # noqa: BLE001
                self.log.error(f'  task-time breakdown failed for {self.name}: {exc!r}')
                self._breakdown = {}
        return self._breakdown


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

    def agg_series(self):
        if self._agg is None:
            self._agg = _aggregate_series(self.profile_series_list)
        return self._agg
