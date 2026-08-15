"""db_reader.py — run DISCOVERY, and the binding from a discovered run to a versioned reader.

This file owns two things and delegates everything else:

  * :class:`RunRef` — one arm's coordinate on the five navigation axes, plus its file paths.
  * :func:`discover_runs` — every arm across a whole run tree, resolved through the run's OWN
    run-tree contract so an old run stays navigable after the tree shape moves on.

All *reading* lives in `Visualization/readers/`, one vetted implementation per sim-DB schema.
That split is the point: `discover_runs` answers "what is here", the readers answer "what does it
say", and a schema change touches only the second.

Both symbols must stay DEFINED IN THIS FILE.  `context/verify_context.py` matches
``^\\s*(def|class)\\s+discover_runs\\b`` against this exact path, driven by the reader anchors in
`context/artifacts.yml` for warehouse_db, sim_db and keyframes_db.  A re-export from elsewhere
does not satisfy that regex.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from Schema import identity as _identity
from Visualization.readers import reader_for
from Visualization.readers.base import _ro
# Imported for their REGISTRATION side effect: `Schema/` imports no writer, so a family exists
# in the registry only once its own module has been loaded, and `_identity.check_or_warn` below
# would raise KeyError instead of checking anything.  `Picking_Data` also carries keyframes_db.
from Optimization.persistence import Picking_Data as _picking_data  # noqa: F401
from Optimization.persistence import Warehouse_Data as _warehouse_data  # noqa: F401

# The navigation axes the viewer builds its cascading selectors from.  Order = selector order:
# warehouse -> warehouse type -> pick config -> cell -> assignment function.
NAV_AXES = ('pair', 'channel', 'config', 'cell', 'strategy')

AXIS_LABELS = {
    'pair': 'Warehouse',
    'channel': 'Warehouse type',
    'config': 'Pick config',
    'cell': 'Layout / scheduler cell',
    'strategy': 'Assignment function',
}


@dataclass
class RunRef:
    """One arm: where it is on the navigation axes, and which files hold it."""

    id: str              # stable id: "<cell>/<pair>/<config>[/<channel>]/<strategy>"
    label: str
    cell: str            # what-if cell (aisle-split x zoning x scheduler)
    pair: str            # the warehouse (inventory+affinity pair label)
    config: str          # pick-config
    channel: str | None  # 'store' | 'fulfillment'; None on a store-only tree (no channel level)
    strategy: str        # the assignment-function arm
    sim_db: str
    warehouse_db: str
    keyframe_db: str
    run_id: int
    n_batches: int
    # Two runs may only be compared — or share a colour authority — when this matches.  Arms from
    # cells with different aisle layouts would otherwise be drawn with aisle ids that silently
    # mean different things.
    warehouse_fingerprint: str | None = None
    viz_cache: str = ''
    _reader: object = field(default=None, repr=False, compare=False)

    @property
    def axis_values(self) -> dict:
        """This run's coordinate on every navigation axis — what the UI filters on."""
        return {'cell': self.cell, 'pair': self.pair, 'config': self.config,
                'channel': self.channel, 'strategy': self.strategy}

    def reader(self, verify: bool = False):
        """The vetted reader for this arm, bound once and reused.

        Readers hold no live connection, so one instance is safe to share across request
        threads; every query opens and closes its own read-only connection.

        The sim DB's identity is `reader_for`'s job — that binding IS the sim-schema check, and
        an unknown shape there raises `UnsupportedSimSchema`.  Its two companion files have no
        such gate, so they get one here: geometry read from an unexpected `warehouse.db` draws a
        plausible warehouse that is quietly wrong, which is the same failure in a different
        artifact.

        WARNS rather than raising.  The viewer is an interactive, strictly read-only explorer of
        an archive that spans two months of schema evolution; refusing to open a 2026-06 run
        outright is a worse outcome than drawing it with the differing columns named in the log,
        and nothing here is published.  The paths that WRITE from a file, or publish a number
        from one, use the hard `identity.check` instead.
        """
        if self._reader is None:
            _identity.check_or_warn(self.warehouse_db, 'warehouse_db', verify=True)
            if self.keyframe_db:
                _identity.check_or_warn(self.keyframe_db, 'keyframes_db', verify=True)
            self._reader = reader_for(
                self.sim_db, self.warehouse_db, self.run_id,
                keyframe_db=self.keyframe_db, viz_cache=self.viz_cache,
                pinned_schema_id=_pinned_schema_id(self.viz_cache), verify=verify)
        return self._reader


def _pinned_schema_id(viz_cache: str) -> str | None:
    """The schema id a previous precompute derived and cached, if any.

    Every DB in the archive predates the `sim_schema_id` column, so without this the id is
    re-derived (~10 PRAGMA round trips) on every binding, times hundreds of arms.
    """
    if not viz_cache or not os.path.exists(viz_cache):
        return None
    try:
        con = _ro(viz_cache)
        try:
            row = con.execute(
                "SELECT value FROM cache_meta WHERE key='sim_schema_id'").fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except Exception:                            # noqa: BLE001 - a bad cache must never block a read
        return None


def _nearest_warehouse_db(sim_db: str, rt=None, cell: str | None = None,
                          pair: str | None = None) -> str | None:
    """The warehouse.db an arm was simulated against.

    With a resolver (`rt` + the arm's cell/pair), the contract answers directly:
    ``rt.warehouse_db(cell, pair)``.  The walk-up below is the documented NO-CONTRACT fallback —
    legacy runs have no descriptor, so the nearest `warehouse.db` on the ancestor chain is the
    best available guess.  It is kept even when the resolver path misses, because a hand-arranged
    tree (an arm copied out for inspection) answers only to the walk.
    """
    if rt is not None and cell and pair:
        cand = rt.warehouse_db(cell, pair)
        if os.path.exists(cand):
            return cand
    d = os.path.dirname(os.path.abspath(sim_db))
    for _ in range(4):
        cand = os.path.join(d, 'warehouse.db')
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    return None


def _warehouse_fingerprint(warehouse_db: str) -> str | None:
    try:
        con = _ro(warehouse_db)
        try:
            row = con.execute('SELECT warehouse_fingerprint FROM warehouse_stats '
                              'ORDER BY id DESC LIMIT 1').fetchone()
            return row['warehouse_fingerprint'] if row else None
        finally:
            con.close()
    except Exception:                            # noqa: BLE001 - pre-fingerprint or unreadable
        return None


_FP_INDEX_CACHE: dict[tuple, dict[str, str]] = {}
_DISCOVER_CACHE: dict[tuple, list] = {}


def _tree_stamp(base_dir: str) -> tuple:
    """A cheap key that changes when the tree gains an arm or is replaced.

    One `os.walk` (~0.15 s even on the results drive) over the run descriptor's mtime plus the
    number of `sim_*.db` files.  A finished run is static, so this is stable; a run still being
    written gains arms, and the count catches that without re-opening 272 databases.
    """
    layout = os.path.join(base_dir, 'run_layout.json')
    try:
        stamp = os.stat(layout).st_mtime_ns if os.path.exists(layout) else 0
    except OSError:
        stamp = 0
    n_sim = 0
    for _root, _dirs, files in os.walk(base_dir):
        n_sim += sum(1 for f in files
                     if f.startswith('sim_') and f.endswith('.db')
                     and not f.endswith('.keyframes.db'))
    return (os.path.abspath(base_dir), stamp, n_sim)


def _warehouse_fp_index(base_dir: str) -> dict[str, str]:
    """Map warehouse_fingerprint -> warehouse.db across the tree, so a run resolves its warehouse
    even if folders were renamed or moved after the run finished.

    Cached on the tree stamp.  This walks the WHOLE run tree — up to ~500 GB — and it used to run
    on every /api/runs request.
    """
    key = _tree_stamp(base_dir)
    hit = _FP_INDEX_CACHE.get(key)
    if hit is not None:
        return hit

    index: dict[str, str] = {}
    for root, _dirs, files in os.walk(base_dir):
        if 'warehouse.db' in files:
            wh = os.path.join(root, 'warehouse.db')
            fp = _warehouse_fingerprint(wh)
            if fp and fp not in index:
                index[fp] = wh
    _FP_INDEX_CACHE.clear()                      # only the current tree is ever wanted
    _FP_INDEX_CACHE[key] = index
    return index


def _read_run_meta(sim_db: str) -> dict | None:
    """First run's identity from a sim DB, tolerant of the pre-identity schema."""
    try:
        con = _ro(sim_db)
        try:
            row = con.execute(
                'SELECT * FROM simulation_runs ORDER BY run_id LIMIT 1').fetchone()
        finally:
            con.close()
    except Exception:                            # noqa: BLE001 - not a sim DB / unreadable
        return None
    if row is None:
        return None
    keys = set(row.keys())
    get = lambda k: (row[k] if k in keys else None)      # noqa: E731 - terse by design
    return {
        'run_id': int(row['run_id']),
        'n_batches': int(get('n_batches') or 0),
        'strategy_key': get('strategy_key'),
        'pair_label': get('pair_label'),
        'config_label': get('config_label'),
        'warehouse_fingerprint': get('warehouse_fingerprint'),
        'sim_schema_id': get('sim_schema_id'),
    }


def viz_cache_path(base_dir: str, cell: str, pair: str, config: str,
                   channel: str | None, strategy: str, rt=None) -> str:
    """Where the derived sidecar for one arm lives.

    ``<run_root>/_viz/<cell>/<pair>/<config>[/<channel>]/<arm>.viz.db`` — under the driver's
    reserved ``_`` prefix (``runschema.schema.RESERVED_PREFIX``), which every tree walker skips.
    The sidecar is DECLARED in the run-tree contract (`viz_cache_db`), so with a resolver the
    template comes from the run's own schema document; the hand-join below is the documented
    NO-CONTRACT fallback for legacy trees, kept byte-identical to what the template renders.

    NOT ``sim_<arm>.viz.db`` beside the sim DB: that name satisfies all three predicates of
    ``runlayout._sim_dbs_in`` (starts with ``sim_``, ends ``.db``, does not end
    ``.keyframes.db``), so it would surface as an extra ARM through ``resolver.sim_dbs()`` and
    crash ``Diagnostics/replay_run.py`` on a table it does not have.
    """
    if rt is not None:
        try:
            return rt.path('viz_cache_db', cell=cell, pair=pair, config=config,
                           channel=channel or None, strategy=strategy)
        except KeyError:                         # contract predates the viz_cache_db artifact
            pass
    parts = [base_dir, '_viz', cell, pair, config] + ([channel] if channel else [])
    return os.path.join(*parts, f'{strategy}.viz.db')


def discover_runs(base_dir: str) -> list[RunRef]:
    """One RunRef per strategy run across the WHOLE run tree, via the versioned resolver.

    `base_dir` is the RUN ROOT (the directory holding `run_layout.json`), not a cell directory:
    `runschema.resolver_for` reads the run's own `run_layout.json` and spans all its cells.

    Rename-proof: strategy/pair/config labels come from the DB's stored identity (falling back to
    the file and directory names), and each run's warehouse.db is matched by
    warehouse_fingerprint (falling back to the nearest warehouse.db by path).  The CELL, however,
    exists only in the path — sim_*.db has no cell column — so it always comes from the resolver.

    Cached on the tree stamp: identifying 272 arms costs ~5.7 s of sim-DB opens on the results
    drive, and `/api/runs` is hit on every page load.  Cached RunRefs keep their bound readers,
    which is the point — readers hold no live connection, so sharing one across request threads
    is safe.
    """
    if not os.path.isdir(base_dir):
        return []
    key = _tree_stamp(base_dir)
    hit = _DISCOVER_CACHE.get(key)
    if hit is not None:
        return hit
    runs = _discover_uncached(base_dir)
    _DISCOVER_CACHE.clear()
    _DISCOVER_CACHE[key] = runs
    return runs


def _discover_uncached(base_dir: str) -> list[RunRef]:
    runs: list[RunRef] = []
    rt = None
    try:
        from Optimization.runschema import resolver_for
        rt = resolver_for(base_dir)
        walk = rt.sim_dbs()
    except Exception:                            # noqa: BLE001 - unresolvable / pre-contract tree
        # Last resort so a bare cell directory still opens: walk it as a single implicit cell.
        from Optimization.runschema.runlayout import iter_sim_dbs
        cell = os.path.basename(os.path.abspath(base_dir).rstrip('/\\'))
        walk = ((cell, cr, db) for cr, db in iter_sim_dbs(base_dir))

    fp_index = _warehouse_fp_index(base_dir)
    for cell, cr, sim_db in walk:
        fn = os.path.basename(sim_db)
        meta = _read_run_meta(sim_db)
        if meta is None:
            continue
        strategy = meta['strategy_key'] or fn[4:-3]
        pair_lbl = meta['pair_label'] or cr.pair
        cfg_lbl = meta['config_label'] or cr.config
        # cr.pair (the DIRECTORY name), not pair_lbl: the resolver renders paths, and a renamed
        # pair label from the DB's identity is exactly what a path must not be built from.
        wh = (fp_index.get(meta['warehouse_fingerprint'])
              or _nearest_warehouse_db(sim_db, rt=rt, cell=cell, pair=cr.pair))
        if not wh:
            continue
        kf = rt.keyframe_db(sim_db) if rt is not None else (
            os.path.splitext(sim_db)[0] + '.keyframes.db')
        chan = cr.channel
        rid = '/'.join(p for p in (cell, pair_lbl, cfg_lbl, chan, strategy) if p)
        runs.append(RunRef(
            id=rid,
            label=' · '.join(p for p in (cell, pair_lbl, cfg_lbl, chan, strategy) if p),
            cell=cell, pair=pair_lbl, config=cfg_lbl, channel=chan, strategy=strategy,
            sim_db=sim_db, warehouse_db=wh,
            keyframe_db=kf if os.path.exists(kf) else '',
            run_id=meta['run_id'], n_batches=meta['n_batches'],
            warehouse_fingerprint=meta['warehouse_fingerprint'],
            viz_cache=viz_cache_path(base_dir, cell, pair_lbl, cfg_lbl, chan, strategy, rt=rt),
        ))
    return runs


def run_index(base_dir: str) -> dict:
    """{schema_id, schema_short, axes, axis_labels, runs} — the navigation contract for the UI.

    `axes` holds the distinct values actually present, so the front end never hardcodes a level.
    A store-only run yields `channel: []`; the UI HIDES that selector rather than inventing a
    value, which is the same optionality the run-tree contract declares.
    """
    runs = discover_runs(base_dir)
    schema_id = schema_short = None
    try:
        from Optimization.runschema import resolver_for
        rt = resolver_for(base_dir)
        schema_id, schema_short = rt.schema_id, rt.schema_short
    except Exception:                            # noqa: BLE001 - unresolvable / legacy tree
        pass
    axes = {a: sorted({getattr(r, a) for r in runs if getattr(r, a)}) for a in NAV_AXES}
    return {
        'schema_id': schema_id,
        'schema_short': schema_short,
        'axes': axes,
        'axis_order': list(NAV_AXES),
        'axis_labels': AXIS_LABELS,
        'runs': [{'id': r.id, 'label': r.label, 'n_batches': r.n_batches,
                  'warehouse_fingerprint': r.warehouse_fingerprint,
                  'has_cache': bool(r.viz_cache and os.path.exists(r.viz_cache)),
                  **r.axis_values} for r in runs],
    }
