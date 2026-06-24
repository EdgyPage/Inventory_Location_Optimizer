"""server.py — DB-backed replay & compare viewer.

Serves finished simulation runs (no live sim) for the web viewer: discover runs by name,
warehouse geometry, per-(run,batch) reconstruction, and static per-bin layout scores. All
reconstruction is read-only and scoped per request (see db_reader); sim DBs are ~550 MB so
nothing loads whole tables.

Run base directory resolution (first that exists):
    --base CLI arg  →  $COMPARISON_OUTPUT_DIR  →  ./ (cwd)

Endpoints
    GET /                         index.html
    GET /<file>                   static asset
    GET /api/runs                 [{id,label,pair,config,strategy,n_batches}, ...]
    GET /api/geometry?run=<id>    {aisles:[...], grid_cols}
    GET /api/batch?run=<id>&batch=<b>   reconstructed batch payload
    GET /api/scores?run=<id>      {key: static_layout_score}
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from flask import Flask, jsonify, request, send_from_directory, abort

import db_reader as R


def _resolve_base() -> str:
    ap = argparse.ArgumentParser(description='DB-backed warehouse replay & compare viewer')
    ap.add_argument('base_dir', nargs='?', default=None,
                    help='comparison output dir to serve (positional)')
    ap.add_argument('--base', default=None, help='comparison output dir to serve')
    ap.add_argument('--port', type=int, default=5000)
    args, _ = ap.parse_known_args()
    base = args.base_dir or args.base or os.environ.get('COMPARISON_OUTPUT_DIR') or os.getcwd()
    base = base.strip().strip('"').strip("'").rstrip('\\/')   # tolerate trailing slash/quote
    return os.path.abspath(base), args.port


_BASE, _PORT = _resolve_base()
app = Flask(__name__, static_folder='static')

# Run index, refreshed on demand so newly-finished runs appear without a restart.
_RUNS: dict = {}


def _refresh_runs() -> None:
    global _RUNS
    _RUNS = {r.id: r for r in R.discover_runs(_BASE)}


_refresh_runs()
print(f'Serving runs from: {_BASE}', flush=True)
if _RUNS:
    print(f'  {len(_RUNS)} run(s) found.  http://localhost:{_PORT}', flush=True)
else:
    print(f'  WARNING: 0 runs found under this directory.  Pass the comparison_* dir, e.g.\n'
          f'    python server.py "<...>/comparison_YYYYMMDD_HHMMSS"\n'
          f'  (looks for <pair>/warehouse.db + <pair>/<config>/sim_*.db).  '
          f'http://localhost:{_PORT}', flush=True)


def _run_or_404(rid: str):
    if rid not in _RUNS:
        _refresh_runs()                      # maybe a new run landed
    run = _RUNS.get(rid)
    if run is None:
        abort(404, description=f'unknown run id: {rid}')
    return run


@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


@app.route('/api/runs')
def api_runs():
    _refresh_runs()
    return jsonify({'base': _BASE, 'runs': R.run_summaries(_BASE)})


@app.route('/api/geometry')
def api_geometry():
    run = _run_or_404(request.args.get('run', ''))
    return jsonify(R.read_geometry(run))


def _batch_arg() -> int:
    try:
        return int(request.args.get('batch', 0))
    except ValueError:
        abort(400, description='batch must be an integer')


@app.route('/api/batch')
def api_batch():
    run = _run_or_404(request.args.get('run', ''))
    return jsonify(R.reconstruct_batch(run, _batch_arg()))


@app.route('/api/overview')
def api_overview():
    """Zoomed-out per-aisle heatmap + aisle-level picker paths (cheap; 157 cells)."""
    run = _run_or_404(request.args.get('run', ''))
    return jsonify(R.reconstruct_overview(run, _batch_arg()))


@app.route('/api/aisle')
def api_aisle():
    """Drill-in: full bin detail + bin-level events for one aisle (bounded payload)."""
    run = _run_or_404(request.args.get('run', ''))
    try:
        aisle = int(request.args.get('aisle', 0))
    except ValueError:
        abort(400, description='aisle must be an integer')
    return jsonify(R.reconstruct_aisle(run, _batch_arg(), aisle))


@app.route('/api/scores')
def api_scores():
    run = _run_or_404(request.args.get('run', ''))
    return jsonify(R.bin_scores(run.sim_db, run.warehouse_db, run.run_id))


if __name__ == '__main__':
    app.run(debug=False, port=_PORT, threaded=True)
