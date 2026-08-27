"""base.py — the SQLite implementation of `SimReader`, shared by every vetted schema.

One instance per arm.  Holds paths and small memos, never a live connection: `server.py` runs
threaded and `sqlite3.Connection` objects are `check_same_thread=True`, so a cached connection
crossing a request boundary raises.  Read-only URI opens are sub-millisecond against a 1 GB file
— trivial next to the ~0.5 s keyframe read they precede — so every query opens and closes its own.

The reconstruction contract is `RECONSTRUCTION.md` §1, and `state_at` has three implementations of
it because a run can carry three different records:

  1. **span index** — a fresh sidecar whose `bin_span` was built from the bin-mutation log.  One
     indexed lookup per frame, exact at every batch.
  2. **live log fold** — the run has `bin_placement`/`bin_eviction` but no (fresh) sidecar: take
     the nearest keyframe and apply the log forward.  Exact, and bounded by the keyframe interval.
  3. **keyframe + picks** — the pre-log archive.  Its `bin_inventory` (a table no run writes any
     more) records picks and *never* restocks, so rolling deltas forward loses 59% of a real
     warehouse within five batches; this path is exact only AT a keyframe and says
     `exact: false` everywhere else.

Which one served a frame is visible in the payload: `exact` is the honest answer, never the
convenient one.

That `exact` is NOT `capability.provenance(cap)['exact']`, and the two must not be conflated.  A
capability's `exact` grades the SOURCE at its own phase — `CAP_KEYFRAMES.exact` is True because a
keyframe is an exact snapshot OF ITS OWN BATCH.  The payload's `exact` grades THIS FRAME, which for
the same source is `batch == kf` and false everywhere in between.  So the frame payload keeps its
own `exact` / `restocks_pending` / `note` contract (`protocol.py`, and `static/views/aisle.js` and
`diff.js` read those three by name); provenance is for a consumer that reports which source it
chose, which the reader does through `capabilities()` instead.
"""
from __future__ import annotations

import os
import sqlite3

import importlib
import json

from Optimization.persistence.Picking_Data import SIM_CAPABILITIES, SIM_DB_FAMILY
from Schema import compat as _compat
from Schema import connect as _connect
from Schema import dataset as _dataset
from Schema import identity as _identity
from Schema.capability import probe
from Visualization.readers.protocol import (
    CAP_AISLE_METRICS, CAP_BIN_LOG, CAP_BIN_SCORES, CAP_KEYFRAMES,
    CAP_SKU_SCORES, CAP_VIZ_CACHE,
)

# ── what `capabilities()` probes the sim DB for ──────────────────────────────────────────────
# Deliberately NOT `SIM_CAPABILITIES.values()`.  `Schema.capability.probe` skips every entry whose
# `table` is None, so the two sidecar capabilities drop out on their own — but two table-backed
# entries would NOT, and neither belongs in a table sweep here:
#
#   bin_log         answered by `_log_start()` and passed through `probe(extra=...)`, so the
#                   MIN(batch_id) this reader needs anyway serves both questions (see below);
#   bin_inventory   read by `_state_without_keyframes`, never reported.  Probing it would add a
#                   name to `/api/capabilities` that no view requires and nothing has ever seen.
#
# So this is the reporting subset, taken BY NAME from the shared registry rather than by writing
# the table names out a second time.
_PROBED = tuple(SIM_CAPABILITIES[name] for name in
                (CAP_AISLE_METRICS, CAP_BIN_SCORES, CAP_SKU_SCORES))

# Hue anchors are assigned per FAMILY, and a family is (handling_type, unit_type, storage_size).
# Measured on the production warehouse: that is 13 families across 384 aisles, which fits the
# ~12 distinguishable categorical hues the eye actually has.  Adding `category` to the key —
# i.e. using the full BinKey — would give 63, far past the point where hue stops being readable;
# instead category orders aisles WITHIN a family so same-category aisles land adjacent in the
# family's hue band.  See README.md, "Colour".
_FAMILY_KEYS = ('handling_type', 'unit_type', 'storage_size')


#: Every declared-shape column this tool touches, and HOW (phase-2 of the staged
#: semantics gate).  A PURE LITERAL: Tests/architecture/test_column_semantics.py
#: AST-reads it and validates against Schema/semantics.py without importing this
#: module, so declaring costs no dependency.
SEMANTIC_USES = {'sim_db': {
    'simulation_runs.run_id': 'read',
    'sku_scores.sku': 'read', 'sku_scores.map_target': 'read',
    'sku_scores.labor_cost': 'read', 'sku_scores.handle_var': 'read',
    'sku_scores.expected_popularity': 'read', 'sku_scores.expected_labor': 'read',
    'sku_scores.equilibrium_qty': 'read', 'sku_scores.reorder_point': 'read',
    'sku_scores.lead_time_mean': 'read',
    'bin_placement.batch_id': 'read', 'bin_placement.seq': 'read',
    'bin_placement.aisle_id': 'read', 'bin_placement.bayX': 'read',
    'bin_placement.bayY': 'read', 'bin_placement.sku': 'read',
    'bin_placement.qty': 'read',
    'bin_eviction.batch_id': 'read', 'bin_eviction.seq': 'read',
    'bin_eviction.aisle_id': 'read', 'bin_eviction.bayX': 'read',
    'bin_eviction.bayY': 'read',
    'picks.quantity': 'sum', 'picks.aisle_id': 'read', 'picks.bayX': 'read',
    'picks.bayY': 'read', 'picks.batch_id': 'read',
}}

def _ro(path: str) -> sqlite3.Connection:
    """Open `path` strictly read-only — `Schema.connect.read_only`, the sanctioned opener.

    Never `Picking_Data._open_db` (its `PRAGMA journal_mode=WAL` is a WRITE), and never
    immutable: the viewer may be watching a run that is still being written.  This used to be
    a hand-rolled URI connect; the broker-boundary ratchet now forbids `sqlite3.connect`
    outside `Schema/connect.py`.
    """
    return _connect.read_only(path)


def _int_list(values) -> str:
    """`1,2,3` for an IN clause, every element forced through int().

    The only place a request parameter reaches SQL text; sqlite3 cannot parameterize IN, so the
    cast is the guard.  Callers must not pass anything else.
    """
    return ','.join(str(int(v)) for v in values)


#: family -> the module whose import registers that family's named queries (the PUBLISHER).
#: Composition must never race an unregistered vocabulary, so `_composed_sql` imports the
#: publisher lazily before asking the registry.
_FAMILY_SOURCES = {
    'sim_db': 'Optimization.persistence.Picking_Data',
    'keyframes_db': 'Optimization.persistence.Picking_Data',
    'viz_cache_db': 'Visualization.cache_schema',
    'warehouse_db': 'Optimization.persistence.Warehouse_Data',
}

#: (family, query, schema_id) -> SQL text.  Module-level: the composition is a pure function
#: of the committed store, so every reader (and thread) shares one cache.
_SQL_CACHE: dict = {}
#: (family, schema_id) -> {table: frozenset(columns)} from the committed/declared shape.
_SURFACE_CACHE: dict = {}


def _composed_sql(family: str, name: str, schema_id: str) -> str:
    """The per-vintage SQL for a named query — `dataset.sql_for`, memoised.

    THE broker seam: a publisher-side `dataset.override(...)` registered for an old vintage
    changes what this returns, and no reader method moves.
    """
    key = (family, name, schema_id)
    if key not in _SQL_CACHE:
        importlib.import_module(_FAMILY_SOURCES[family])
        _SQL_CACHE[key] = _dataset.sql_for(family, name, schema_id)
    return _SQL_CACHE[key]


def _family_surface(family: str, schema_id: str) -> dict:
    """{table: columns} of the committed (or declared) shape for `schema_id` — the no-PRAGMA
    answer to has-table questions (closes the bin_eviction probe gap)."""
    key = (family, schema_id)
    if key not in _SURFACE_CACHE:
        fam = _identity.get(family)
        shape = (fam.declared_shape() if schema_id == fam.declared_id()
                 else _compat.load_shape(family, schema_id))
        _SURFACE_CACHE[key] = _compat._surface(shape) if shape is not None else {}
    return _SURFACE_CACHE[key]


# ── what this reader READS, declared — the compatibility gate CI validates ──────────────────
# The first Requires in the viewer package.  Guaranteed-surface reads only; the conditional
# tables (`bin_placement`, `bin_eviction`, `bin_inventory`) are deliberately ABSENT — every
# read of those sits behind a capability probe or a `_has` surface check, per the
# CONDITIONAL_READS doctrine.  `simulation_runs`/`sku_scores` declare ANY_COLUMNS because
# their reads are shape-following by design (`run_meta` splats the row; `sku_scores` returns
# whatever the vintage carries).
REQUIRES = _compat.Requires(
    family='sim_db',
    label='Visualization reader (SqliteSimReader)',
    tables={
        'simulation_runs': _compat.ANY_COLUMNS,
        'batch_stats': ('run_id', 'batch_id', 'duration', 'num_tasks', 'total_items',
                        'reorder_placements'),
        # `sim_time` is the intra-batch clock (_apply_picks_upto_t) — read since the first
        # viewer build, declared here for the first time.
        'picks': ('run_id', 'batch_id', 'aisle_id', 'bayX', 'bayY', 'sku', 'quantity',
                  'sim_time'),
        'picker_events': ('run_id', 'batch_id', 'id', 'time', 'picker_id', 'event_type',
                          'aisle_id', 'bayX', 'bayY', 'sku', 'quantity', 'bins_completed',
                          'total_bins', 'items_picked', 'total_items'),
        'task_stats': ('run_id', 'batch_id', 'aisle_id', 'picker_id', 'task_start_time',
                       'task_end_time', 'duration', 'W', 'lift_sum', 'num_bins_visited',
                       'total_items', 'is_outlier'),
        'bin_scores': ('run_id', 'aisle_id', 'bayX', 'bayY', 'travel_d', 'height_mult',
                       'layout_score', 'map_pref'),
        'sku_scores': _compat.ANY_COLUMNS,
    })

REQUIRES_KEYFRAMES = _compat.Requires(
    family='keyframes_db',
    label='Visualization reader (keyframe sidecar)',
    tables={'bin_keyframe': ('run_id', 'batch_id', 'aisle_id', 'bayX', 'bayY', 'sku', 'qty')})

REQUIRES_VIZ_CACHE = _compat.Requires(
    family='viz_cache_db',
    label='Visualization reader (viz sidecar)',
    tables={
        'cache_meta': ('key', 'value'),
        'bin_span': ('run_id', 'aisle_id', 'bayX', 'bayY', 't_from', 't_to', 'sku',
                     'qty_at_from'),
        'sku_rank': ('run_id', 'sku', 'rank', 'picks', 'units', 'first_batch', 'last_batch'),
        'sku_series': _compat.ANY_COLUMNS,           # shape-following: columns ARE the payload
        'final_home': ('run_id', 'sku', 'aisle_id', 'bayX', 'bayY', 'qty', 'n_homes',
                       'home_aisles', 'batch_id'),
        'aisle_batch_rollup': _compat.ANY_COLUMNS,   # served by the named query, DDL-order cols
    })

# NOT `warehouse_stats.warehouse_fingerprint`: that column is absent on vintage 342d31313d08
# and served by the override — declaring it here would claim it is guaranteed, which is
# exactly the lie the override exists to avoid.
REQUIRES_WAREHOUSE = _compat.Requires(
    family='warehouse_db',
    label='Visualization reader (warehouse geometry)',
    tables={'aisle_layout': ('aisle_id', 'handling_type', 'category', 'unit_type',
                             'storage_size', 'bay_x', 'bay_y')})


class SqliteSimReader:
    """Read one arm's `sim_<arm>.db` (+ keyframes, warehouse, and optional viz cache)."""

    #: Overridden by each vetted schema module.
    SCHEMA_IDS: tuple[str, ...] = ()

    def __init__(self, sim_db: str, warehouse_db: str, run_id: int,
                 keyframe_db: str = '', viz_cache: str = '', schema_id: str = '',
                 schema_source: str = '', warehouse_schema_id: str | None = None,
                 keyframe_schema_id: str | None = None):
        self.sim_db = sim_db
        self.warehouse_db = warehouse_db
        self.keyframe_db = keyframe_db if keyframe_db and os.path.exists(keyframe_db) else ''
        # NOT resolved to '' here: a reader is memoised on its RunRef, so pinning existence at
        # bind time makes an arm permanently cache-blind if the sidecar is built afterwards —
        # which is the normal order (open the viewer, then precompute). `_cache_conn` re-checks.
        self.viz_cache = viz_cache or ''
        self.run_id = int(run_id)
        self._schema_id = schema_id
        self._schema_source = schema_source
        # Companion ids as captured by RunRef.reader()'s check_or_warn (None on the WARN
        # path); _sid falls back to the family's declared id there, so composed SQL for an
        # unvetted companion is the canonical text — byte-equivalent to the old raw string,
        # same degrade.
        self._warehouse_schema_id = warehouse_schema_id
        self._keyframe_schema_id = keyframe_schema_id
        self._memo: dict = {}

    def _sid(self, family: str) -> str:
        if family == 'sim_db':
            return self._schema_id
        if family == 'warehouse_db':
            return self._warehouse_schema_id or _identity.get(family).declared_id()
        if family == 'keyframes_db':
            return self._keyframe_schema_id or _identity.get(family).declared_id()
        # viz_cache_db: `_cache_conn` gates every read on `cache_freshness`, whose
        # identity.check calls anything non-current 'stale' — a non-declared shape never
        # reaches a cache read.
        return _identity.get(family).declared_id()

    def _sql(self, family: str, name: str) -> str:
        return _composed_sql(family, name, self._sid(family))

    def _has(self, family: str, table: str) -> bool:
        return table in _family_surface(family, self._sid(family))

    # ── identity ─────────────────────────────────────────────────────────────────

    def schema_id(self) -> str:
        return self._schema_id

    def schema_source(self) -> str:
        """'stamped' | 'pinned' | 'derived' — how the id was obtained."""
        return self._schema_source

    def capabilities(self) -> frozenset[str]:
        """Which optional data this arm actually HAS — probed for ROWS, then memoised.

        A present-but-empty table is the common case, not an edge case: `aisle_metrics` and
        `reorder_queue` are only written by strategies that maintain that state, and are empty
        for every arm of the current production run.  Reporting them as absent is what lets the
        UI hide a panel instead of rendering 0.0 as though it were a measurement.  That is why
        the sweep is `Schema.capability.probe` — whose `has_rows` answers False for a missing
        table and for an empty one alike — and not an introspection of `sqlite_master`.

        Three capabilities are not table questions and reach the probe through `extra`:

          * **keyframes** — a sibling `.keyframes.db` with rows for this run;
          * **bin_log** — `_log_start()`, i.e. `MIN(batch_id) FROM bin_placement`.  Semantically
            this is the row probe (`batch_id` is in the primary key, so it is never NULL and a
            non-None MIN means at least one row) — but it is also MEMOISED and shared with
            `_state_from_log`, which needs the value itself and not merely its existence.  Letting
            `probe` re-derive it would issue a second query, and would leave `_memo['log_start']`
            cold for the request that needs it.  Behaviour first: the existing call stays, and its
            answer is handed in;
          * **viz_cache** — sidecar FRESHNESS, which is not a property of the sim DB at all.

        Only the table probes are memoised.  `viz_cache` is deliberately re-checked on every call:
        a cache built while the viewer is running must appear without a restart.
        """
        if 'caps' in self._memo:
            # The cache flag is re-checked on every call; only the table probes are memoised,
            # since those need a full sim-DB open and cannot change for a finished run.
            fixed = self._memo['caps'] - {CAP_VIZ_CACHE}
            return fixed | ({CAP_VIZ_CACHE} if self.cache_status() == 'fresh' else set())
        extra = set()
        if self.keyframe_db and self.keyframe_batches():
            extra.add(CAP_KEYFRAMES)
        if self._log_start() is not None:
            extra.add(CAP_BIN_LOG)
        # Freshness, not mere existence: a stale sidecar is not a capability, it is a hazard.
        # Deliberately NOT memoised with the rest — a cache built while the viewer is running
        # must become visible without a restart.
        if self.cache_status() == 'fresh':
            extra.add(CAP_VIZ_CACHE)
        con = _ro(self.sim_db)
        try:
            self._memo['caps'] = probe(con, _PROBED, self.run_id, extra=extra)
        finally:
            con.close()
        return self._memo['caps']

    # ── static, per run ──────────────────────────────────────────────────────────

    def run_meta(self) -> dict:
        if 'meta' in self._memo:
            return self._memo['meta']
        con = _ro(self.sim_db)
        try:
            row = con.execute('SELECT * FROM simulation_runs WHERE run_id=?',
                              (self.run_id,)).fetchone()
        finally:
            con.close()
        keys = set(row.keys()) if row is not None else set()
        meta = {k: row[k] for k in keys} if row is not None else {}
        batches = self.batch_index()
        meta.update({
            'schema_id': self._schema_id,
            'schema_source': self._schema_source,
            'capabilities': sorted(self.capabilities()),
            'batches': [b['batch_id'] for b in batches],
            'keyframes': self.keyframe_batches(),
            # n_batches over-counts: a batch that produced no tasks writes no batch_stats row.
            'n_batches_recorded': len(batches),
        })
        self._memo['meta'] = meta
        return meta

    def aisle_geometry(self) -> list[dict]:
        """One row per aisle — never a per-bin list.

        Materializing every bin for all 384 aisles is ~396,500 dicts and a ~40 MB JSON response
        per pane; the grid is regenerated from bay_x x bay_y wherever it is actually drawn.

        Also assigns each aisle its palette coordinates: `family_index` (which of the 13 hue
        anchors) and `family_ord` (its rank inside that family's hue band, ordered by category
        then aisle_id so same-category aisles sit adjacent).
        """
        if 'geom' in self._memo:
            return self._memo['geom']
        con = _ro(self.warehouse_db)
        try:
            rows = [dict(r) for r in con.execute(
                self._sql('warehouse_db', 'aisle_layout_geometry'))]
        finally:
            con.close()

        families = sorted({tuple(r[k] for k in _FAMILY_KEYS) for r in rows})
        fam_index = {f: i for i, f in enumerate(families)}
        by_family: dict[tuple, list[dict]] = {}
        for r in rows:
            by_family.setdefault(tuple(r[k] for k in _FAMILY_KEYS), []).append(r)
        for fam, members in by_family.items():
            members.sort(key=lambda r: (r['category'], r['aisle_id']))
            for ordinal, r in enumerate(members):
                r['family'] = '/'.join(str(p) for p in fam)
                r['family_index'] = fam_index[fam]
                r['family_ord'] = ordinal
                r['family_size'] = len(members)

        for r in rows:
            r['bay_x'], r['bay_y'] = int(r['bay_x'] or 0), int(r['bay_y'] or 0)
            r['capacity'] = r['bay_x'] * r['bay_y']
        self._memo['geom'] = rows
        self._memo['n_families'] = len(families)
        return rows

    def batch_index(self) -> list[dict]:
        """Per-batch timing, from `batch_stats` — the list legitimately has holes.

        A batch that produced no tasks `continue`s before its stats are appended, so
        `range(n_batches)` walks batches with no timing, no events and no deltas.
        """
        if 'batches' in self._memo:
            return self._memo['batches']
        kf = set(self.keyframe_batches())
        con = _ro(self.sim_db)
        try:
            rows = [{
                'batch_id': int(r['batch_id']),
                'duration': float(r['duration']),
                'num_tasks': int(r['num_tasks']),
                'total_items': int(r['total_items']),
                'reorder_placements': int(r['reorder_placements'] or 0),
                'is_keyframe': int(r['batch_id']) in kf,
            } for r in con.execute(self._sql('sim_db', 'batch_timing'),
                                   {'run_id': self.run_id})]
        finally:
            con.close()
        self._memo['batches'] = rows
        return rows

    def keyframe_batches(self) -> list[int]:
        """The batches with an exact spatial snapshot — the animation grid."""
        if 'kf_batches' in self._memo:
            return self._memo['kf_batches']
        out: list[int] = []
        if self.keyframe_db:
            con = _ro(self.keyframe_db)
            try:
                out = [int(r['batch_id']) for r in con.execute(
                    self._sql('keyframes_db', 'keyframe_batches'),
                    {'run_id': self.run_id})]
            except sqlite3.OperationalError:
                out = []
            finally:
                con.close()
        self._memo['kf_batches'] = out
        return out

    def bin_scores(self, aisles: list[int] | None = None) -> dict:
        """Static per-bin layout cost.  ALWAYS scope on a real warehouse.

        Unscoped this is 396,500 rows and a ~20 MB response; the viewer only ever draws the
        aisles on screen.
        """
        con = _ro(self.sim_db)
        try:
            rows = con.execute(
                self._sql('sim_db', 'bin_scores_scoped'),
                {'run_id': self.run_id,
                 'aisles': json.dumps([int(a) for a in aisles]) if aisles else None}).fetchall()
        except sqlite3.OperationalError:
            return {'layout': {}, 'map_pref': {}, 'has_map': False}
        finally:
            con.close()
        layout, pref = {}, {}
        for r in rows:
            key = f"{r['aisle_id']},{r['bayX']},{r['bayY']}"
            layout[key] = round(r['layout_score'], 4)
            if r['map_pref'] is not None:
                pref[key] = round(r['map_pref'], 4)
        return {'layout': layout, 'map_pref': pref, 'has_map': bool(pref)}

    def sku_scores(self, skus: list[int] | None = None) -> dict:
        # RAW by design: the output columns ARE the physical columns (shape-following); the
        # scope rides one JSON parameter instead of interpolated text.
        where = ' AND sku IN (SELECT value FROM json_each(?))' if skus else ''
        args = ((self.run_id, json.dumps([int(s) for s in skus])) if skus
                else (self.run_id,))
        con = _ro(self.sim_db)
        try:
            rows = con.execute(
                f'SELECT * FROM sku_scores WHERE run_id=?{where}', args).fetchall()
        except sqlite3.OperationalError:
            return {}
        finally:
            con.close()
        drop = {'run_id', 'sku'}
        return {str(r['sku']): {k: r[k] for k in r.keys() if k not in drop} for r in rows}

    # ── state ────────────────────────────────────────────────────────────────────

    def _keyframe_at_or_before(self, batch: int) -> int | None:
        kfs = self.keyframe_batches()
        prior = [k for k in kfs if k <= batch]
        return max(prior) if prior else None

    def _log_start(self) -> int | None:
        """The first batch this run recorded a PLACE for, or None if it has no log.

        `MIN(batch_id)` on the `(run_id, batch_id, seq)` primary key, so it is an index seek,
        not a scan.  None is the normal answer for every arm in the archive — those runs predate
        `bin_placement` — which is why the missing table is caught rather than raised.
        """
        if 'log_start' in self._memo:
            return self._memo['log_start']
        out = None
        con = _ro(self.sim_db)
        try:
            row = con.execute('SELECT MIN(batch_id) FROM bin_placement WHERE run_id=?',
                              (self.run_id,)).fetchone()
            out = None if row is None or row[0] is None else int(row[0])
        except sqlite3.OperationalError:              # table absent: a pre-log run
            out = None
        finally:
            con.close()
        self._memo['log_start'] = out
        return out

    def _span_index(self) -> sqlite3.Connection | None:
        """An open sidecar, but ONLY when its `bin_span` came from the bin-mutation log.

        A v1 sidecar, or one rebuilt from the keyframes because the arm predates the log, holds
        spans on the keyframe grid.  Reading those as if they were exact between keyframes is
        precisely the error this whole change removes, so the source is checked, not assumed.
        """
        from Visualization.cache_schema import SPAN_SOURCE_LOG
        cache = self._cache_conn()
        if cache is None:
            return None
        try:
            row = cache.execute(self._sql('viz_cache_db', 'cache_meta_value'),
                                {'key': 'span_source'}).fetchone()
            if row is not None and row[0] == SPAN_SOURCE_LOG:
                return cache
        except sqlite3.Error:
            pass
        cache.close()
        return None

    def _pending_restocks(self, keyframe: int, batch: int) -> int:
        """Restock placements this frame cannot show: batches (keyframe, batch].

        The keyframe already includes its OWN batch's restocks — `check_reorders()` runs before
        the snapshot is taken.
        """
        if batch <= keyframe:
            return 0
        return sum(b['reorder_placements'] for b in self.batch_index()
                   if keyframe < b['batch_id'] <= batch)

    def state_at(self, batch: int, aisles: list[int] | None = None,
                 t: float | None = None) -> dict:
        """Occupied bins at (batch, t).

        Tries the three records in cost order — cached span index, live log fold, keyframe —
        and returns the first that can answer.  The first two are exact at EVERY batch; the
        third is exact only AT a keyframe and says so.
        """
        batch = int(batch)
        state = self._state_from_spans(batch, aisles, t)
        if state is None:
            state = self._state_from_log(batch, aisles, t)
        if state is None:
            state = self._state_from_keyframes(batch, aisles, t)
        return state

    def _state_from_spans(self, batch: int, aisles, t) -> dict | None:
        """The frame from the sidecar's log-built span index. None = no such index.

        The span gives the bin's SKU and the qty it was filled with at `t_from`; the qty NOW is
        that minus the picks since.  A keyframe at or below `batch` is used as the qty anchor
        whenever one exists — not for correctness, but to bound the pick scan to one keyframe
        interval instead of the whole run.  Without keyframes the answer is the same, read from
        a longer range.
        """
        cache = self._span_index()
        if cache is None:
            return None
        scope = f' AND aisle_id IN ({_int_list(aisles)})' if aisles else ''
        try:
            rows = cache.execute(
                self._sql('viz_cache_db', 'bin_span_scoped'),
                {'run_id': self.run_id, 'batch': batch,
                 'aisles': json.dumps([int(a) for a in aisles]) if aisles else None}).fetchall()
        except sqlite3.Error:
            return None
        finally:
            cache.close()
        if not rows:
            # Either the batch is outside the cached run or the scope holds no occupied bin.
            # An empty warehouse is not a state this simulation produces, so fall through
            # rather than assert an exact empty frame.
            return None

        kf = self._keyframe_at_or_before(batch)
        kf_state = self._keyframe_state(kf, aisles) if kf is not None else {}
        bins: dict[str, dict] = {}
        anchor: dict[str, int] = {}                   # bin -> batch its qty is known at
        for r in rows:
            if r['sku'] is None:
                continue
            key = f"{r['aisle_id']},{r['bayX']},{r['bayY']}"
            sku, t_from = int(r['sku']), int(r['t_from'])
            known = kf_state.get(key)
            # The keyframe only anchors a span it actually falls inside, and only when the two
            # agree on the SKU: a disagreement means one of the records is wrong, and the log is
            # the one this frame is built from.
            if kf is not None and t_from <= kf and known is not None and known['sku'] == sku:
                bins[key] = {'sku': sku, 'qty': known['qty']}
                anchor[key] = kf
            else:
                bins[key] = {'sku': sku, 'qty': int(r['qty_at_from'])}
                anchor[key] = t_from

        lo = min(anchor.values())
        con = _ro(self.sim_db)
        try:
            if batch > lo:
                # Grouped per (bin, batch) rather than per bin: each bin subtracts only the
                # picks at or after ITS OWN anchor — earlier ones belong to a previous unit.
                for r in con.execute(
                        f'SELECT aisle_id, bayX, bayY, batch_id, SUM(quantity) n FROM picks '
                        f'WHERE run_id=? AND batch_id>=? AND batch_id<?{scope} '
                        f'GROUP BY aisle_id, bayX, bayY, batch_id', (self.run_id, lo, batch)):
                    key = f"{r['aisle_id']},{r['bayX']},{r['bayY']}"
                    if int(r['batch_id']) >= anchor.get(key, batch):
                        _deplete(bins, r['aisle_id'], r['bayX'], r['bayY'], r['n'])
            self._apply_picks_upto_t(con, bins, batch, t, scope)
        finally:
            con.close()
        return {'batch': batch, 'keyframe': kf, 't': t, 'bins': bins,
                'exact': True, 'restocks_pending': 0, 'note': ''}

    def _state_from_log(self, batch: int, aisles, t) -> dict | None:
        """The frame folded live from `bin_placement` + `bin_eviction` + `picks`.

        Used when the run HAS a log but no fresh sidecar. The nearest keyframe is the base and
        the log carries it forward, so the work is bounded by the keyframe interval. With no
        keyframe below, an empty warehouse is the base — valid only when the log starts at batch
        0, i.e. it recorded the initial fill; a resumed run whose log starts later cannot be
        anchored and falls through.
        """
        start = self._log_start()
        if start is None:
            return None
        kf = self._keyframe_at_or_before(batch)
        if kf is None and start != 0:
            return None
        base = kf if kf is not None else 0
        scope = f' AND aisle_id IN ({_int_list(aisles)})' if aisles else ''

        bins = self._keyframe_state(kf, aisles) if kf is not None else {}
        events: dict[int, list] = {}                  # batch -> [(kind, seq, key, sku, qty)]
        con = _ro(self.sim_db)
        try:
            # kind 0 = EVICT, 1 = PLACE, so sorting a batch's events replays the runner's order:
            # the reloader, then check_reorders, then the picks.
            # Gated on the VINTAGE's surface, not on bin_placement's probe: a vintage carrying
            # placements without the eviction table used to raise `no such table` out of
            # state_at here, while precompute guarded the same read.  (The bug fix.)
            if self._has('sim_db', 'bin_eviction'):
                for r in con.execute(
                        f'SELECT batch_id, seq, aisle_id, bayX, bayY FROM bin_eviction '
                        f'WHERE run_id=? AND batch_id>? AND batch_id<=?{scope}',
                        (self.run_id, base, batch)):
                    events.setdefault(int(r['batch_id']), []).append(
                        (0, int(r['seq']), f"{r['aisle_id']},{r['bayX']},{r['bayY']}", None, 0))
            for r in con.execute(
                    f'SELECT batch_id, seq, aisle_id, bayX, bayY, sku, qty FROM bin_placement '
                    f'WHERE run_id=? AND batch_id>? AND batch_id<=?{scope}',
                    (self.run_id, base, batch)):
                events.setdefault(int(r['batch_id']), []).append(
                    (1, int(r['seq']), f"{r['aisle_id']},{r['bayX']},{r['bayY']}",
                     int(r['sku']), int(r['qty'])))
            picks: dict[int, list] = {}
            if batch > base:
                for r in con.execute(
                        f'SELECT batch_id, aisle_id, bayX, bayY, SUM(quantity) n FROM picks '
                        f'WHERE run_id=? AND batch_id>=? AND batch_id<?{scope} '
                        f'GROUP BY batch_id, aisle_id, bayX, bayY', (self.run_id, base, batch)):
                    picks.setdefault(int(r['batch_id']), []).append(
                        (r['aisle_id'], r['bayX'], r['bayY'], r['n']))

            for b in range(base, batch + 1):
                for kind, _seq, key, sku, qty in sorted(events.get(b, ())):
                    if kind:
                        bins[key] = {'sku': sku, 'qty': qty}
                    else:
                        bins.pop(key, None)
                for aisle, bx, by, n in picks.get(b, ()):
                    _deplete(bins, aisle, bx, by, n)
            self._apply_picks_upto_t(con, bins, batch, t, scope)
        finally:
            con.close()
        return {'batch': batch, 'keyframe': kf, 't': t, 'bins': bins,
                'exact': True, 'restocks_pending': 0, 'note': ''}

    def _keyframe_state(self, keyframe: int | None, aisles) -> dict:
        """`{bin: {sku, qty}}` at one keyframe — the post-restock, pre-pick start of that batch."""
        bins: dict[str, dict] = {}
        if keyframe is None or not self.keyframe_db:
            return bins
        con = _ro(self.keyframe_db)
        try:
            for r in con.execute(
                    self._sql('keyframes_db', 'keyframe_state_scoped'),
                    {'run_id': self.run_id, 'batch_id': keyframe,
                     'aisles': json.dumps([int(a) for a in aisles]) if aisles else None}):
                if r['qty'] > 0:
                    bins[f"{r['aisle_id']},{r['bayX']},{r['bayY']}"] = {
                        'sku': int(r['sku']), 'qty': int(r['qty'])}
        finally:
            con.close()
        return bins

    def _apply_picks_upto_t(self, con, bins: dict, batch: int, t, scope: str) -> None:
        """Subtract batch `batch`'s own picks up to sim_time `t`.  No-op when `t` is None.

        This is the intra-batch clock: `picks.sim_time` is the finest resolution the record has,
        and it is unaffected by the log (restocks carry no sub-batch time — `check_reorders()`
        runs entirely between two batches' simulations).
        """
        if t is None:
            return
        for r in con.execute(
                f'SELECT aisle_id, bayX, bayY, SUM(quantity) n FROM picks '
                f'WHERE run_id=? AND batch_id=? AND sim_time<=?{scope} '
                f'GROUP BY aisle_id, bayX, bayY', (self.run_id, batch, float(t))):
            _deplete(bins, r['aisle_id'], r['bayX'], r['bayY'], r['n'])

    def _state_from_keyframes(self, batch: int, aisles: list[int] | None = None,
                              t: float | None = None) -> dict:
        """The pre-log frame: nearest keyframe below, minus the picks since.

        Exact when `batch` IS a keyframe batch.  Otherwise depletion-exact but missing the
        restocks of the batches in between, which is reported rather than hidden.
        """
        kf = self._keyframe_at_or_before(batch)
        scope = f' AND aisle_id IN ({_int_list(aisles)})' if aisles else ''

        if kf is None:
            kfs = self.keyframe_batches()
            if kfs:
                # Keyframes exist but all come AFTER this batch — reachable on a resumed run.
                # Saying "no keyframes in this run" here would be false, and returning the
                # delta-rebuilt frame silently would be worse.
                return {'batch': batch, 'keyframe': None, 't': t, 'bins': {},
                        'exact': False, 'restocks_pending': self._pending_restocks(0, batch),
                        'note': f'batch {batch} precedes the first keyframe ({kfs[0]}); no exact '
                                f'spatial frame exists below it'}
            # No keyframes at all (keyframe_interval=0) AND no log.  The only baseline left is
            # an ARCHIVED run's bin_inventory snapshot at its first batch; deltas cannot rebuild
            # restocks, so this frame is exact only at that batch — and the table may itself be
            # absent, which that method reports rather than raising.
            return self._state_without_keyframes(batch, aisles, t)

        bins = self._keyframe_state(kf, aisles)

        # Apply picks: whole batches [kf, batch), then batch itself up to t.
        con = _ro(self.sim_db)
        try:
            if batch > kf:
                for r in con.execute(
                        f'SELECT aisle_id, bayX, bayY, SUM(quantity) n FROM picks '
                        f'WHERE run_id=? AND batch_id>=? AND batch_id<?{scope} '
                        f'GROUP BY aisle_id, bayX, bayY', (self.run_id, kf, batch)):
                    _deplete(bins, r['aisle_id'], r['bayX'], r['bayY'], r['n'])
            self._apply_picks_upto_t(con, bins, batch, t, scope)
        finally:
            con.close()

        pending = self._pending_restocks(kf, batch)
        return {
            'batch': batch, 'keyframe': kf, 't': t,
            'bins': bins,
            'exact': batch == kf,
            'restocks_pending': pending,
            'note': ('' if batch == kf else
                     f'frame built from keyframe {kf}; at least {pending} restock placements '
                     f'between batches {kf + 1} and {batch} are not represented'),
        }

    def _state_without_keyframes(self, batch: int, aisles, t) -> dict:
        """Last-resort path: an ARCHIVED run written with keyframe_interval=0.

        Still reachable, and not dead code: it is the only record such an arm has.  A run from
        this build never lands here twice over — it has a log, so `_state_from_log` folds it from
        an empty warehouse exactly, and `bin_inventory` is not even in its schema.

        `bin_inventory` (archive-only; no longer written) holds one full snapshot at the run's
        FIRST batch — not necessarily 0 on a resumed run — and depletion-only deltas after it.
        Ordering is by `batch_id, id`: the loop is last-write-wins and a bin can receive rows
        from two branches in one batch, so `batch_id` alone is not a total order.

        The table's ABSENCE is a normal outcome, not an error: it means the file is new enough
        to have no such record and old enough (keyframe_interval=0) to have no keyframe either.
        Say so, rather than raising `no such table` at a caller three layers up.
        """
        scope = f' AND aisle_id IN ({_int_list(aisles)})' if aisles else ''
        bins: dict[str, dict] = {}
        con = _ro(self.sim_db)
        try:
            base = con.execute('SELECT MIN(batch_id) b FROM bin_inventory WHERE run_id=?',
                               (self.run_id,)).fetchone()['b']
            for r in con.execute(
                    f'SELECT aisle_id, bayX, bayY, sku, post_qty FROM bin_inventory '
                    f'WHERE run_id=? AND batch_id<=?{scope} ORDER BY batch_id, id',
                    (self.run_id, batch)):
                key = f"{r['aisle_id']},{r['bayX']},{r['bayY']}"
                if r['post_qty'] > 0:
                    bins[key] = {'sku': int(r['sku']), 'qty': int(r['post_qty'])}
                else:
                    bins.pop(key, None)
        except sqlite3.OperationalError:              # no bin_inventory -> nothing to rebuild from
            return {
                'batch': batch, 'keyframe': None, 't': t, 'bins': {}, 'exact': False,
                'restocks_pending': self._pending_restocks(0, batch),
                'note': 'this run carries no spatial record at all: no keyframes '
                        '(keyframe_interval=0), no bin-mutation log, and no bin_inventory. '
                        'Re-run the arm to get one.',
            }
        finally:
            con.close()
        return {
            'batch': batch, 'keyframe': None, 't': t, 'bins': bins,
            # `t` is not applied on this path, so a frame carrying one is never exact.
            'exact': base is not None and batch == base and t is None,
            'restocks_pending': self._pending_restocks(base or 0, batch),
            'note': 'no keyframes in this run; rebuilt from depletion deltas, which cannot '
                    'show restocks. Re-run with --keyframe-interval > 0 for exact frames.',
        }

    def aisle_state(self, batch: int, aisle: int, t: float | None = None) -> dict:
        aisle = int(aisle)
        state = self.state_at(batch, aisles=[aisle], t=t)
        geom = {int(a['aisle_id']): a for a in self.aisle_geometry()}.get(aisle, {})
        return {**state, 'aisle_id': aisle, 'geom': geom,
                'events': self.events(batch, aisle=aisle)}

    def bin_history(self, aisle: int, bayX: int, bayY: int) -> list[dict]:
        """Everything this bin held, one row per occupancy.

        Served from the sidecar's `bin_span` when present — where, on a log-built cache, the
        `t_from`/`t_to` bounds are the real batches the bin held that SKU rather than the
        keyframes it was observed at.  The live fallback is a scan of `bin_keyframe`, whose PK
        starts `(run_id, batch_id)`, so there is no usable per-aisle index and it costs ~5 s on a
        production DB.  That is exactly why the sidecar exists.
        """
        aisle, bayX, bayY = int(aisle), int(bayX), int(bayY)
        cache = self._cache_conn()
        if cache is not None:
            try:
                rows = [dict(r) for r in cache.execute(
                    self._sql('viz_cache_db', 'bin_history'),
                    {'run_id': self.run_id, 'aisle_id': aisle, 'bayX': bayX, 'bayY': bayY})]
                return rows
            except sqlite3.Error:
                pass
            finally:
                cache.close()
        if not self.keyframe_db:
            return []
        con = _ro(self.keyframe_db)
        try:
            # The TWIN of the sidecar query: same logical columns, aliased in SQL - each
            # keyframe observation is a degenerate span.
            return [dict(r) for r in con.execute(
                self._sql('keyframes_db', 'bin_history'),
                {'run_id': self.run_id, 'aisle_id': aisle, 'bayX': bayX, 'bayY': bayY})]
        finally:
            con.close()

    # ── events ───────────────────────────────────────────────────────────────────

    def events(self, batch: int, aisle: int | None = None) -> list[dict]:
        """Timed picker events for one batch.  Times are BATCH-RELATIVE (each batch ~0-based)."""
        con = _ro(self.sim_db)
        try:
            return [{
                'time': round(r['time'], 4), 'picker_id': int(r['picker_id']),
                'event_type': r['event_type'], 'aisle_id': r['aisle_id'],
                'location': ([r['aisle_id'], r['bayX'], r['bayY']]
                             if r['aisle_id'] is not None and r['bayX'] is not None else None),
                'sku': r['sku'], 'quantity': r['quantity'],
                'bins_completed': r['bins_completed'], 'total_bins': r['total_bins'],
                'items_picked': r['items_picked'], 'total_items': r['total_items'],
            } for r in con.execute(
                self._sql('sim_db', 'picker_events_timeline'),
                {'run_id': self.run_id, 'batch_id': int(batch),
                 'aisle_id': int(aisle) if aisle is not None else None})]
        finally:
            con.close()

    def tasks(self, batch: int | None = None, aisle: int | None = None) -> list[dict]:
        """Per-task rows — one picker's single-aisle ordered pick sequence."""
        # Two registered variants rather than one OR-form: the (run_id, batch_id) index
        # serves the interactive per-batch path only with a real equality predicate.
        if batch is not None:
            sql = self._sql('sim_db', 'task_rows_at_batch')
            params = {'run_id': self.run_id, 'batch_id': int(batch),
                      'aisle_id': int(aisle) if aisle is not None else None}
        else:
            sql = self._sql('sim_db', 'task_rows_all')
            params = {'run_id': self.run_id,
                      'aisle_id': int(aisle) if aisle is not None else None}
        con = _ro(self.sim_db)
        try:
            return [{
                'batch_id': int(r['batch_id']), 'aisle_id': int(r['aisle_id']),
                'picker_id': int(r['picker_id']),
                'task_start_time': round(r['task_start_time'], 4),
                'task_end_time': round(r['task_end_time'], 4),
                'duration': round(r['duration'], 4), 'W': round(r['W'], 4),
                'lift_sum': round(r['lift_sum'], 4),
                'num_bins_visited': int(r['num_bins_visited']),
                'total_items': int(r['total_items']), 'is_outlier': int(r['is_outlier']),
            } for r in con.execute(sql, params)]
        finally:
            con.close()

    # ── derived rollups (sidecar-backed, with live fallbacks) ────────────────────

    def cache_status(self) -> str:
        """'fresh' | 'stale' | 'absent' | 'partial' — re-checked, never pinned.

        Freshness matters on the READ path, not just at build time: an arm rebuilt by a
        strategy-granularity resume gets a brand-new sim DB and its own `run_id = 1`, so a stale
        sidecar's rows collide by key and the viewer would keep serving the PREVIOUS run's
        `final_home` (the colour authority) and occupancy with nothing to signal it.
        """
        from Visualization.cache_schema import cache_freshness
        return cache_freshness(self.viz_cache, self.sim_db, self.keyframe_db, self.warehouse_db)

    def _cache_conn(self) -> sqlite3.Connection | None:
        """An open sidecar connection, or None when there is nothing trustworthy to read."""
        if not self.viz_cache or self.cache_status() != 'fresh':
            return None
        try:
            return _ro(self.viz_cache)
        except sqlite3.Error:
            return None

    def aisle_rollup(self, batch: int) -> list[dict]:
        """Per-aisle aggregates for one batch: occupancy, picks, visits.

        The live fallback rebuilds whole-warehouse state (~0.7 s) purely to count occupied bins
        per aisle — which is what the sidecar's `aisle_batch_rollup` precomputes away.
        """
        batch = int(batch)
        cache = self._cache_conn()
        if cache is not None:
            try:
                rows = [dict(r) for r in cache.execute(
                    self._sql('viz_cache_db', 'aisle_rollup'),
                    {'run_id': self.run_id, 'batch_id': batch})]
                if rows:
                    return rows
            except sqlite3.Error:
                pass
            finally:
                cache.close()

        state = self.state_at(batch)
        final = self.final_home().get('homes', {})
        occ, qty, skus, homes = {}, {}, {}, {}
        for key, info in state['bins'].items():
            aid = int(key.split(',', 1)[0])
            occ[aid] = occ.get(aid, 0) + 1
            qty[aid] = qty.get(aid, 0) + info['qty']
            skus.setdefault(aid, set()).add(info['sku'])
            home = final.get(str(info['sku']))
            if home and aid in home.get('home_aisles', ()):
                homes[aid] = homes.get(aid, 0) + 1
        con = _ro(self.sim_db)
        try:
            picks = {int(r['aisle_id']): (int(r['picks']), int(r['units_picked']))
                     for r in con.execute(self._sql('sim_db', 'pick_load_by_aisle'),
                                          {'run_id': self.run_id, 'batch_id': batch})}
            tasks = {int(r['aisle_id']): (int(r['visits']), float(r['task_secs'] or 0.0))
                     for r in con.execute(self._sql('sim_db', 'task_load'),
                                          {'run_id': self.run_id, 'batch_id': batch})}
        finally:
            con.close()
        return [{
            'run_id': self.run_id, 'batch_id': batch, 'aisle_id': a['aisle_id'],
            'occupied': occ.get(a['aisle_id'], 0), 'capacity': a['capacity'],
            'qty': qty.get(a['aisle_id'], 0), 'n_skus': len(skus.get(a['aisle_id'], ())),
            'picks': picks.get(a['aisle_id'], (0, 0))[0],
            'units_picked': picks.get(a['aisle_id'], (0, 0))[1],
            'visits': tasks.get(a['aisle_id'], (0, 0.0))[0],
            'task_secs': tasks.get(a['aisle_id'], (0, 0.0))[1],
            # Same shape as the cached path, so a view never sees a column appear and disappear
            # with the cache.  home_match needs final_home, which is cheap enough to reuse here.
            'home_match': homes.get(a['aisle_id'], 0),
        } for a in self.aisle_geometry()]

    def top_skus(self, n: int = 50) -> list[dict]:
        """The n most-picked SKUs.  The live fallback is a ~24 s full scan of `picks`."""
        n = int(n)
        cache = self._cache_conn()
        if cache is not None:
            try:
                rows = [dict(r) for r in cache.execute(
                    self._sql('viz_cache_db', 'sku_rank_top'),
                    {'run_id': self.run_id, 'n': n})]
                if rows:
                    return rows
            except sqlite3.Error:
                pass
            finally:
                cache.close()
        con = _ro(self.sim_db)
        try:
            rows = con.execute(self._sql('sim_db', 'sku_rank_live'),
                               {'run_id': self.run_id, 'n': n}).fetchall()
        finally:
            con.close()
        return [{'sku': int(r['sku']), 'rank': i + 1, 'picks': int(r['picks']),
                 'units': int(r['units']), 'first_batch': int(r['first_batch']),
                 'last_batch': int(r['last_batch'])} for i, r in enumerate(rows)]

    def sku_series(self, skus: list[int]) -> dict:
        """Per-batch pick counts for the given SKUs.  Indexed by `(run_id, sku)`, so cheap live.

        The sidecar's `sku_series` holds only the top-N window, so a mixed request is a PARTIAL
        hit: taking it as complete would report every SKU outside the window as never picked —
        the exact failure `sku_rank` exists to prevent.  So the cache is used for what it has and
        the remainder falls through to the live query, which is index-served and cheap.
        """
        wanted = [int(s) for s in skus]
        if not wanted:
            return {}
        out: dict[str, list] = {}
        cache = self._cache_conn()
        if cache is not None:
            try:
                # RAW by design: the sidecar's physical columns ARE the payload.
                rows = [dict(r) for r in cache.execute(
                    'SELECT * FROM sku_series WHERE run_id=?'
                    ' AND sku IN (SELECT value FROM json_each(?)) ORDER BY sku, batch_id',
                    (self.run_id, json.dumps(wanted)))]
                out = _group_by_sku(rows)
            except sqlite3.Error:
                out = {}
            finally:
                cache.close()

        missing = [s for s in wanted if str(s) not in out]
        if not missing:
            return out
        con = _ro(self.sim_db)
        try:
            rows = [{'sku': int(r['sku']), 'batch_id': int(r['batch_id']),
                     'picks': int(r['picks']), 'units': int(r['units'])}
                    for r in con.execute(self._sql('sim_db', 'sku_series_live'),
                                         {'run_id': self.run_id,
                                          'skus': json.dumps(missing)})]
        finally:
            con.close()
        out.update(_group_by_sku(rows))
        return out

    def final_home(self) -> dict:
        """Per-SKU destination at the LAST keyframe — the colour authority.

        `{sku: {aisle_id, bayX, bayY, qty, n_homes, home_aisles: [...]}}`.

        A SKU almost never lands in exactly one bin: the warehouse is sized
        ``bins >= n_skus * 1.1``, and measured on the production run **64.3% of SKUs hold
        multiple bins at the last keyframe — and only 9.7% of those keep every replica in one
        aisle** (median span 2 aisles, max 8).  So `home_aisles` is the set, and it is what
        "is this item home?" must be tested against; scoring against a single bin would paint
        the majority of perfectly-placed replicas as misplaced.

        `aisle_id`/`bayX`/`bayY` are the PRIMARY home — largest qty, ties broken by lowest
        (aisle, bayX, bayY) so the choice is deterministic.  That one is what the hue and
        lightness are derived from; the set is what the chroma is derived from.
        """
        cache = self._cache_conn()
        if cache is not None:
            try:
                rows = [dict(r) for r in cache.execute(
                    self._sql('viz_cache_db', 'final_home'), {'run_id': self.run_id})]
                if rows:
                    return {'batch': rows[0]['batch_id'], 'homes': {
                        str(r['sku']): {
                            'aisle_id': r['aisle_id'], 'bayX': r['bayX'], 'bayY': r['bayY'],
                            'qty': r['qty'], 'n_homes': r['n_homes'],
                            'home_aisles': [int(a) for a in str(r['home_aisles']).split(',')
                                            if a],
                        } for r in rows}}
            except sqlite3.OperationalError:
                pass
            finally:
                cache.close()

        kfs = self.keyframe_batches()
        if not kfs:
            return {'batch': None, 'homes': {}}
        last = kfs[-1]
        best: dict[int, dict] = {}
        aisles: dict[int, set] = {}
        con = _ro(self.keyframe_db)
        try:
            for r in con.execute(self._sql('keyframes_db', 'keyframe_bins_nonzero'),
                                 {'run_id': self.run_id, 'batch_id': last}):
                sku = int(r['sku'])
                cand = {'aisle_id': int(r['aisle_id']), 'bayX': int(r['bayX']),
                        'bayY': int(r['bayY']), 'qty': int(r['qty']), 'n_homes': 1}
                aisles.setdefault(sku, set()).add(cand['aisle_id'])
                cur = best.get(sku)
                if cur is None:
                    best[sku] = cand
                    continue
                cur['n_homes'] += 1
                cand['n_homes'] = cur['n_homes']
                if _better_home(cand, cur):
                    best[sku] = cand
        finally:
            con.close()
        for sku, home in best.items():
            home['home_aisles'] = sorted(aisles[sku])
        return {'batch': last, 'homes': {str(k): v for k, v in best.items()}}


# ── helpers ──────────────────────────────────────────────────────────────────────

def _deplete(bins: dict, aisle, bayX, bayY, n) -> None:
    """Subtract `n` picked units from a bin, dropping it when it empties."""
    key = f'{aisle},{bayX},{bayY}'
    cur = bins.get(key)
    if cur is None:
        return
    remaining = cur['qty'] - int(n or 0)
    if remaining > 0:
        cur['qty'] = remaining
    else:
        bins.pop(key, None)


def _better_home(cand: dict, cur: dict) -> bool:
    """Largest qty wins; ties broken by lowest (aisle, bayX, bayY) so the choice is stable."""
    if cand['qty'] != cur['qty']:
        return cand['qty'] > cur['qty']
    return ((cand['aisle_id'], cand['bayX'], cand['bayY'])
            < (cur['aisle_id'], cur['bayX'], cur['bayY']))


def _group_by_sku(rows: list[dict]) -> dict:
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(str(r['sku']), []).append(
            {k: v for k, v in r.items() if k not in ('sku', 'run_id')})
    return out
