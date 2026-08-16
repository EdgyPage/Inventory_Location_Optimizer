"""server.py — the HTTP surface over the versioned readers.

Routing and argument parsing only. Every route resolves a run id to a `RunRef`, asks its reader,
and jsonifies the plain dict that comes back — there is no data logic here and there should not
be. That is what lets a sim-DB schema change land in `readers/` alone.

    python Visualization/server.py "<run_root>"      # the dir holding run_layout.json
    python Visualization/server.py                   # falls back to $COMPARISON_OUTPUT_DIR

Two properties worth knowing before adding a route:

**Scope every bin-level payload.** On a production warehouse the unscoped forms are 396,500 rows
— `/api/geometry` returned ~40 MB and `/api/scores` ~20 MB per pane before they took `?aisles=`.
Aisle lists are `int()`-cast on the way in; that cast is the only guard between a query parameter
and SQL text, since sqlite3 cannot parameterize `IN`.

**Nothing is fetched per time-step.** `t` is resolved client-side by replaying the batch's event
list, which is already loaded. Routes are run-scoped or batch-scoped, never t-scoped.
"""
from __future__ import annotations

import argparse
import os
import sys

# ── path setup: the repo root, so `Visualization.*` and `Optimization.*` resolve when this file
#    is run as a script.  One of the two legal sys.path bootstraps (CLAUDE.md §2).
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from flask import Flask, abort, jsonify, request, send_from_directory   # noqa: E402

from Schema.identity import SchemaError                                 # noqa: E402
from Visualization.db_reader import discover_runs, run_index            # noqa: E402


def _resolve_base() -> tuple[str, int]:
    ap = argparse.ArgumentParser(description='Spatial run viewer (read-only).')
    ap.add_argument('base_dir', nargs='?', default=None,
                    help='run root to serve; defaults to $COMPARISON_OUTPUT_DIR')
    ap.add_argument('--base', default=None, help='same as the positional form')
    ap.add_argument('--port', type=int, default=5000)
    args, _unknown = ap.parse_known_args()        # tolerate the Flask reloader's argv

    # Importing sim_config is what loads .env; resolve_base_dir is the shared rule every
    # run-tree CLI uses (absolute as-is, bare name against COMPARISON_OUTPUT_DIR).
    from Optimization.config.sim_config import _OUTPUT_DIR
    from Optimization.runschema import resolve_base_dir

    raw = args.base_dir or args.base
    if raw:
        base = resolve_base_dir(raw.strip().strip('"').strip("'").rstrip('\\/'))
    else:
        base = os.path.abspath(_OUTPUT_DIR or os.getcwd())
    return base, args.port


_BASE, _PORT = _resolve_base()
app = Flask(__name__, static_folder='static')


def _run_or_404(rid: str):
    """Resolve a run id.  `discover_runs` is cached on a tree stamp, so this is a dict build."""
    runs = {r.id: r for r in discover_runs(_BASE)}
    run = runs.get(rid)
    if run is None:
        abort(404, description=f'unknown run id: {rid}')
    return run


def _reader(rid: str):
    try:
        return _run_or_404(rid).reader()
    except SchemaError as exc:
        # An unvetted schema is a 501, not a 500: the server works, this file is not supported.
        # The message names the differing tables and columns rather than a bare hash.
        # `SchemaError` is the whole hierarchy — the viewer's own SimSchemaError subclasses it,
        # and the shared pipeline's SchemaDrift/UnsupportedSchema/UnsupportedQuery land here too.
        abort(501, description=str(exc))


def _int_arg(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ''):
        return default
    try:
        return int(raw)
    except ValueError:
        abort(400, description=f'{name} must be an integer')


def _float_arg(name: str) -> float | None:
    raw = request.args.get(name)
    if raw in (None, ''):
        return None
    try:
        return float(raw)
    except ValueError:
        abort(400, description=f'{name} must be a number')


def _aisles_arg() -> list[int] | None:
    """`?aisles=1,2,3` -> [1,2,3].  Every element is int()-cast; see the module docstring."""
    raw = request.args.get('aisles')
    if not raw:
        return None
    try:
        return [int(a) for a in raw.split(',') if a.strip()]
    except ValueError:
        abort(400, description='aisles must be a comma-separated list of integers')


def _skus_arg() -> list[int]:
    raw = request.args.get('skus', '')
    try:
        return [int(s) for s in raw.split(',') if s.strip()]
    except ValueError:
        abort(400, description='skus must be a comma-separated list of integers')


# ── static ───────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# ── navigation ───────────────────────────────────────────────────────────────────

@app.route('/api/runs')
def api_runs():
    """The navigation contract: schema id, the axis values present, and every arm.

    The UI builds its cascading selectors from `axes`/`axis_order`, so adopting a new run-tree
    schema changes what you can navigate by without a single JS edit.  An axis with no values
    (e.g. `channel` on a store-only run) is HIDDEN rather than given an invented value.
    """
    return jsonify({'base': _BASE, **run_index(_BASE)})


@app.route('/api/schema')
def api_schema():
    """The run-tree contract THIS run was written with — not whatever head happens to be."""
    try:
        from Optimization.runschema import contract, resolver_for
        try:
            sid = resolver_for(_BASE).schema_id
        except Exception:                                  # noqa: BLE001 - unresolvable tree
            sid = contract.head()
        doc = contract.load(sid) if sid else None
        if doc is not None:
            return jsonify(doc)
    except Exception:                                      # noqa: BLE001
        pass
    if os.path.exists(os.path.join(app.static_folder, 'schema.json')):
        return send_from_directory(app.static_folder, 'schema.json')
    abort(404, description='no run-tree contract available')


@app.route('/api/capabilities')
def api_capabilities():
    """What this arm actually HAS — probed for rows, not just columns.

    `aisle_metrics` exists in every sim DB but is empty for most arms.  The UI hides that
    panel rather than rendering 0.0 as though it were a measurement.
    """
    reader = _reader(request.args.get('run', ''))
    return jsonify({'schema_id': reader.schema_id(), 'schema_source': reader.schema_source(),
                    'capabilities': sorted(reader.capabilities())})


# ── run-scoped (fetched once, never invalidated by scrubbing) ────────────────────

@app.route('/api/meta')
def api_meta():
    return jsonify(_reader(request.args.get('run', '')).run_meta())


@app.route('/api/geometry')
def api_geometry():
    """One row per aisle, with its palette coordinates.  Never a per-bin list."""
    return jsonify({'aisles': _reader(request.args.get('run', '')).aisle_geometry()})


@app.route('/api/scores')
def api_scores():
    """Static per-bin layout cost.  Scope with `?aisles=` — unscoped this is 396,500 rows."""
    return jsonify(_reader(request.args.get('run', '')).bin_scores(_aisles_arg()))


@app.route('/api/sku_scores')
def api_sku_scores():
    reader = _reader(request.args.get('run', ''))
    return jsonify(reader.sku_scores(_skus_arg() or None))


@app.route('/api/final_home')
def api_final_home():
    """The colour authority: each SKU's destination at the last keyframe.

    `home_aisles` is a SET — most SKUs hold replicas in several aisles, so "is this item home?"
    is tested against the set while hue and lightness come from the primary home.
    """
    return jsonify(_reader(request.args.get('run', '')).final_home())


# ── batch-scoped ─────────────────────────────────────────────────────────────────

@app.route('/api/state')
def api_state():
    """Occupied bins at (batch, t).  Carries `exact` and `restocks_pending`.

    Exact at EVERY batch for a run carrying the bin-mutation log — `state_at` folds
    `bin_placement` + `bin_eviction` + `picks`, which is the complete record of bin state.

    For an ARCHIVED arm (no log) it is exact only at a keyframe batch: that vintage's
    `bin_inventory` never records restocks, so an in-between frame is depletion-exact and
    missing that stretch's restocks.  The payload says which it got; the UI must show it rather
    than drawing a plausible warehouse.
    """
    reader = _reader(request.args.get('run', ''))
    return jsonify(reader.state_at(_int_arg('batch', 0), aisles=_aisles_arg(),
                                   t=_float_arg('t')))


@app.route('/api/aisle')
def api_aisle():
    """Drill-in: one aisle's bins, geometry and timed events."""
    aisle = _int_arg('aisle')
    if aisle is None:
        abort(400, description='aisle is required')
    reader = _reader(request.args.get('run', ''))
    return jsonify(reader.aisle_state(_int_arg('batch', 0), aisle, t=_float_arg('t')))


@app.route('/api/aisle_rollup')
def api_aisle_rollup():
    """Per-aisle aggregates for one batch — occupancy, picks, visits, home-match."""
    reader = _reader(request.args.get('run', ''))
    return jsonify({'aisles': reader.aisle_rollup(_int_arg('batch', 0))})


# ── cross-batch ──────────────────────────────────────────────────────────────────

@app.route('/api/tasks')
def api_tasks():
    """Per-task rows, optionally scoped to one batch and/or aisle."""
    reader = _reader(request.args.get('run', ''))
    return jsonify({'tasks': reader.tasks(batch=_int_arg('batch'), aisle=_int_arg('aisle'))})


@app.route('/api/bin_history')
def api_bin_history():
    """Everything one bin held across the run."""
    aisle, bx, by = _int_arg('aisle'), _int_arg('bayX'), _int_arg('bayY')
    if None in (aisle, bx, by):
        abort(400, description='aisle, bayX and bayY are all required')
    reader = _reader(request.args.get('run', ''))
    return jsonify({'spans': reader.bin_history(aisle, bx, by)})


@app.route('/api/top_skus')
def api_top_skus():
    reader = _reader(request.args.get('run', ''))
    return jsonify({'skus': reader.top_skus(_int_arg('n', 50))})


@app.route('/api/sku_series')
def api_sku_series():
    reader = _reader(request.args.get('run', ''))
    return jsonify({'series': reader.sku_series(_skus_arg())})


_GZIP_MIN = 64 * 1024          # below this the round trip costs more than it saves


@app.after_request
def _gzip(response):
    """Compress large JSON in flight.  Stdlib only — no middleware dependency.

    These payloads are highly repetitive JSON and compress ~10x: the colour authority
    (`/api/final_home`, ~70k SKUs) drops from ~6 MB to a few hundred KB, and a drill-in aisle
    from ~230 KB to ~25 KB.  Run-scoped payloads are fetched once per run, but the viewer opens
    two of them side by side and the difference is a visible pause.
    """
    if (response.direct_passthrough
            or response.status_code < 200 or response.status_code >= 300
            or 'gzip' not in request.headers.get('Accept-Encoding', '').lower()
            or response.content_length is None or response.content_length < _GZIP_MIN
            or 'Content-Encoding' in response.headers):
        return response
    import gzip
    data = gzip.compress(response.get_data(), compresslevel=5)
    response.set_data(data)
    response.headers['Content-Encoding'] = 'gzip'
    response.headers['Content-Length'] = str(len(data))
    response.headers['Vary'] = 'Accept-Encoding'
    return response


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(501)
def _json_error(exc):
    return jsonify({'error': getattr(exc, 'description', str(exc)),
                    'status': getattr(exc, 'code', 500)}), getattr(exc, 'code', 500)


@app.errorhandler(SchemaError)
def _schema_error(exc):
    """Any schema-shaped failure escaping a ROUTE BODY is still a 501, never a Flask 500.

    `_reader` catches the hierarchy at binding; this is the second net, for a vetted-but-
    unservable read inside a route (the CI servability matrix makes that unreachable in
    practice — this handler exists so 'unreachable' never has to be load-bearing)."""
    return jsonify({'error': str(exc), 'status': 501}), 501


def _startup_banner() -> None:
    runs = discover_runs(_BASE)
    print(f'Serving runs from: {_BASE}', flush=True)
    if not runs:
        print('  WARNING: 0 runs found.  Pass the RUN ROOT — the directory holding\n'
              '  run_layout.json, not a cell directory.', flush=True)
        return
    cached = sum(1 for r in runs if r.viz_cache and os.path.exists(r.viz_cache))
    print(f'  {len(runs)} arm(s), {cached} with a derived cache.', flush=True)
    if cached < len(runs):
        print('  Uncached arms work but are slow (a top-N SKU query is a ~24 s scan).\n'
              '  Build one with:  python -m Visualization.precompute "<run_root>" --arms <key>',
              flush=True)
    print(f'  http://localhost:{_PORT}', flush=True)


if __name__ == '__main__':
    _startup_banner()
    # threaded: readers hold no live connection — every query opens and closes its own, so a
    # request thread never touches another thread's sqlite3.Connection.
    app.run(debug=False, port=_PORT, threaded=True)
