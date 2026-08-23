"""test_writer_paths_golden.py — the analysis writers' contract-rendered paths, pinned to the
literals their old hand-joins produced.

The 2026-08 consumer migration moved every analysis-side writer (run_whatif_delta / _labor /
_volume, run_channel_rollup, run_analysis) off hand-joined output paths and
onto the run-tree contract accessors (`rt.path` / `rt.leaf_path` / `rt.aggregate_dir`).  The
migration's whole promise is that NO RENDERED PATH MOVES: every artifact must land at exactly the
byte string the old `os.path.join` calls produced, on BOTH tree shapes (mixed catalog with a
`<channel>` level, store-only without).  This file is that proof — the golden strings below are
hardcoded copies of the old code's joins, deliberately NOT derived from the contract, so a
template edit that would relocate an artifact fails here by name before it ships.

Two deliberate subtleties:

  * The `_aggregate` group key can carry a slash (`ChannelRun.group_key` is `<config>/<channel>`
    on a mixed run — `store/store` is a real key, because the store CONFIG and the store CHANNEL
    share a name).  The old `os.path.join(cell_dir, '_aggregate', cfg)` passed that slash through
    VERBATIM, which on Windows leaves a literal `/` inside an otherwise backslash-separated string;
    `rt.aggregate_dir` renders every separator as `os.sep`.  Same directory on every platform
    (asserted via `os.path.normpath`), and on POSIX the raw strings are identical — the canonical
    form is additionally pinned exactly, so the on-disk location provably cannot move.
  * On a store-only tree the optional channel level must vanish WITH its separator — a rendered
    `.../_aggregate/store/` (trailing sep) or `.../_aggregate/store//` would relocate every
    aggregate PNG.  Asserted exactly against the old two-segment join.

Synthetic descriptors only (write_run_layout stamps the head contract id) — no sim DBs, no disk
tree beyond run_layout.json, deterministic.

Run:  python -m pytest Tests/integration/test_writer_paths_golden.py -q
"""
from __future__ import annotations

import os

from Optimization.runschema import resolver_for
from Optimization.runschema.runlayout import ChannelRun
from Optimization.runschema.sim_manifest import write_run_layout


# ── tree builders (descriptor only — path rendering needs no files) ──────────────

def _descriptor(root, cells, channels, reference):
    write_run_layout(
        str(root), spec='golden', reference=reference,
        cells=[(c, None, {'enabled': False},
                'lpt' if c.endswith('_lpt') else 'round_robin') for c in cells],
        pairs=[('pairA', 'i.db', 'a.db')], store_cfgs=[{'name': 'store'}],
        ff_cfgs=[], channels=channels, arms=None, created='2026-08-15T00:00:00')


def _mixed_tree(tmp_path):
    """A mixed-catalog sweep: two cells, leaves at <cell>/<pair>/<config>/<channel>/."""
    base = tmp_path / 'comparison_whatif_golden_mixed'
    base.mkdir()
    _descriptor(base, ['k1_off_rr', 'k1_off_lpt'], ['store', 'fulfillment'], 'k1_off_rr')
    return str(base)


def _store_only_tree(tmp_path):
    """A store-only run: one cell, leaves directly at <cell>/<pair>/<config>/ (NO channel dir)."""
    base = tmp_path / 'comparison_golden_store_only'
    base.mkdir()
    _descriptor(base, ['k1_off'], ['store'], 'k1_off')
    return str(base)


def _leaf(base, cell, pair, config, channel):
    """A ChannelRun exactly as iter_channel_runs would yield it (channel=None = store-only)."""
    parts = [base, cell, pair, config] + ([channel] if channel else [])
    return ChannelRun(pair, config, channel, os.path.join(*parts))


# ── run-root what-if writers (run_whatif_delta / _labor / _volume) ───────────────

def test_whatif_run_root_outputs_render_the_old_literals(tmp_path):
    """Every cross-cell what-if artifact must stay at `<run_root>/<basename>` — the exact strings
    `os.path.join(base_dir, '<name>')` produced before the migration."""
    base = _mixed_tree(tmp_path)
    rt = resolver_for(base)

    # run_whatif_delta.run() + _write_delta_json()
    assert rt.path('whatif_delta_csv') == os.path.join(base, 'whatif_delta.csv')
    assert rt.path('whatif_delta_json') == os.path.join(base, 'whatif_delta.json')
    assert rt.path('whatif_delta_png') == os.path.join(base, 'whatif_delta.png')

    # run_whatif_labor.run()
    assert rt.path('whatif_labor_csv') == os.path.join(base, 'whatif_labor.csv')
    assert rt.path('whatif_labor_json') == os.path.join(base, 'whatif_labor.json')

    # run_whatif_volume.run()
    assert rt.path('whatif_volume_csv') == os.path.join(base, 'whatif_volume.csv')
    assert rt.path('whatif_volume_json') == os.path.join(base, 'whatif_volume.json')

    # The PNGs render as <glob dir>/<concrete basename>; the glob's star is in the filename
    # segment, so the template's dirname must be the run root itself.
    png_dir = os.path.dirname(rt.path('whatif_labor_pngs'))
    assert png_dir == base
    for name in ('whatif_labor_throughput_scatter.png', 'whatif_labor_saved_bars.png',
                 'whatif_batch_hours_rr_vs_lpt.png', 'whatif_scheduler_uplift.png',
                 'whatif_volume_curves.png', 'whatif_volume_uplift_bars.png'):
        assert os.path.join(png_dir, name) == os.path.join(base, name)


def test_whatif_outputs_render_identically_on_a_store_only_tree(tmp_path):
    """Run-root artifacts are channel-blind: the store-only shape must not perturb them."""
    base = _store_only_tree(tmp_path)
    rt = resolver_for(base)
    assert rt.path('whatif_delta_csv') == os.path.join(base, 'whatif_delta.csv')
    assert rt.path('whatif_volume_json') == os.path.join(base, 'whatif_volume.json')
    assert os.path.dirname(rt.path('whatif_labor_pngs')) == base


# ── cell rollup CSVs (run_channel_rollup) ────────────────────────────────────────

def test_channel_rollup_csvs_render_the_old_literals(tmp_path):
    """rollup() used to join its two CSVs onto the CELL dir it was handed; the contract renders
    `{cell}/<basename>` from the run root — same bytes."""
    base = _mixed_tree(tmp_path)
    rt = resolver_for(base)
    for cell in ('k1_off_rr', 'k1_off_lpt'):
        cell_dir = os.path.join(base, cell)                      # analyze_run's per-cell arg
        assert rt.path('channel_rollup_csv', cell=cell) == \
            os.path.join(cell_dir, 'channel_rollup.csv')
        assert rt.path('channel_rollup_summary_csv', cell=cell) == \
            os.path.join(cell_dir, 'channel_rollup_summary.csv')


# ── per-leaf docs (run_analysis + run_channel_rollup via leaf_path) ──────────────

def test_leaf_docs_render_the_old_literals_on_both_tree_shapes(tmp_path):
    """`rt.leaf_path(cr, ...)` must equal `os.path.join(cr.path, '<basename>')` for the two docs
    the analysis walks on — with a channel level and without one (ChannelRun.channel is None on a
    store-only tree, and nothing may assume the segment exists)."""
    mixed = _mixed_tree(tmp_path)
    rt = resolver_for(mixed)
    for channel in ('store', 'fulfillment'):                     # <config>=store, both channels:
        cr = _leaf(mixed, 'k1_off_rr', 'pairA', 'store', channel)  # store/store is a REAL path
        assert rt.leaf_path(cr, 'sim_meta') == os.path.join(cr.path, 'sim_meta.json')
        assert rt.leaf_path(cr, 'series_json') == os.path.join(cr.path, 'series.json')

    store_only = _store_only_tree(tmp_path)
    rt2 = resolver_for(store_only)
    cr = _leaf(store_only, 'k1_off', 'pairA', 'store', None)     # no channel dir at all
    assert rt2.leaf_path(cr, 'sim_meta') == os.path.join(cr.path, 'sim_meta.json')
    assert rt2.leaf_path(cr, 'series_json') == os.path.join(cr.path, 'series.json')


# ── the aggregate subtree (run_analysis._aggregate_jobs) ─────────────────────────

def test_aggregate_dir_store_only_group_key_is_byte_identical(tmp_path):
    """channel=None: the optional segment must drop WITH its separator — the old literal was
    exactly two joined segments, and a trailing (or doubled) separator would move every
    aggregate output."""
    base = _store_only_tree(tmp_path)
    rt = resolver_for(base)
    cell_dir = os.path.join(base, 'k1_off')
    got = rt.aggregate_dir('k1_off', 'store')                    # group_key = '<config>' only
    assert got == os.path.join(cell_dir, '_aggregate', 'store')
    assert not got.endswith(os.sep) and not got.endswith('/')


def test_aggregate_dir_slash_group_key_lands_in_the_old_directory(tmp_path):
    """The mixed-run group key carries a slash (`store/store` — the store config under the store
    channel).  The old join passed it through verbatim; the resolver renders every separator as
    os.sep.  On POSIX the strings are identical; on Windows the old string held a literal '/'
    inside the last component, so the two are compared as PATHS (normpath) and the canonical
    per-segment form is pinned exactly — either way the directory cannot move."""
    base = _mixed_tree(tmp_path)
    rt = resolver_for(base)
    cell_dir = os.path.join(base, 'k1_off_rr')
    group_key = 'store/store'                                    # ChannelRun.group_key, mixed run

    got = rt.aggregate_dir('k1_off_rr', group_key)
    old_literal = os.path.join(cell_dir, '_aggregate', group_key)     # the pre-migration join
    canonical = os.path.join(cell_dir, '_aggregate', 'store', 'store')

    assert got == canonical
    assert os.path.normpath(got) == os.path.normpath(old_literal)
    if os.sep == '/':                                            # POSIX: raw bytes must agree too
        assert got == old_literal


def test_aggregate_stats_pair_renders_the_tables_literals(tmp_path):
    """The family redesign moved every aggregate stats document into the aggregate tables
    folder.  The two flat-fork templates must render exactly what the aggregate tables
    eval writes — `os.path.join(ctx.out_dir, 'tables', <basename>)` with ctx.out_dir the
    aggregate dir — on BOTH tree shapes, since {channel?} rides the group key."""
    mixed = _mixed_tree(tmp_path)
    rt = resolver_for(mixed)
    agg = rt.aggregate_dir('k1_off_rr', 'store/store')           # mixed group_key = cfg/channel
    assert rt.path('aggregate_stats_summary_csv', cell='k1_off_rr',
                   config='store', channel='store') == \
        os.path.join(agg, 'tables', 'aggregate_summary.csv')
    assert rt.path('aggregate_stats_tests_json', cell='k1_off_rr',
                   config='store', channel='store') == \
        os.path.join(agg, 'tables', 'aggregate_tests.json')
    assert rt.path('aggregate_by_initial_csv', cell='k1_off_rr',
                   config='store', channel='store') == \
        os.path.join(agg, 'tables', 'by_initial_summary.csv')

    store_only = _store_only_tree(tmp_path)
    rt2 = resolver_for(store_only)
    agg2 = rt2.aggregate_dir('k1_off', 'store')                  # store-only: no channel segment
    assert rt2.path('aggregate_stats_summary_csv', cell='k1_off', config='store') == \
        os.path.join(agg2, 'tables', 'aggregate_summary.csv')
    assert rt2.path('aggregate_stats_tests_json', cell='k1_off', config='store') == \
        os.path.join(agg2, 'tables', 'aggregate_tests.json')


def test_leaf_tables_render_under_the_tables_folder(tmp_path):
    """The per-leaf CSV surface moved into one tidy-tables folder; only the long per-batch
    CSV stays at the leaf root (it must survive the parent pre-wipe of the shared tops).
    Pin both facts on both tree shapes."""
    mixed = _mixed_tree(tmp_path)
    rt = resolver_for(mixed)
    cr = _leaf(mixed, 'k1_off_rr', 'pairA', 'store', 'store')
    assert rt.leaf_path(cr, 'batches_long_csv') == os.path.join(cr.path, 'batches_long.csv')
    for name, base in (('per_run_summary_csv', 'per_run_summary.csv'),
                       ('batch_metrics_csv', 'batch_metrics.csv'),
                       ('task_metrics_csv', 'task_metrics.csv'),
                       ('by_initial_summary_csv', 'by_initial_summary.csv'),
                       ('by_initial_tests_json', 'by_initial_tests.json')):
        assert rt.leaf_path(cr, name) == os.path.join(cr.path, 'tables', base), name

    store_only = _store_only_tree(tmp_path)
    rt2 = resolver_for(store_only)
    cr2 = _leaf(store_only, 'k1_off', 'pairA', 'store', None)
    assert rt2.leaf_path(cr2, 'batch_metrics_csv') == \
        os.path.join(cr2.path, 'tables', 'batch_metrics.csv')


# ── a run whose OWN contract predates the volume artifacts ───────────────────────

def test_volume_outputs_render_on_a_run_predating_their_artifacts(tmp_path):
    """Found live on the archive: the 2026-07-29 sweeps stamp contract e69b6d392929, which has NO
    whatif_volume_csv/_json — `rt.path` on it KeyErrors, yet the old hand-join happily wrote
    today's basenames onto that root.  `run_whatif_volume._writer_tree` must therefore fall back
    to the HEAD document for its OUTPUTS (reads keep the run's own contract) and render exactly
    the old literals."""
    from Optimization.run_whatif_volume import _writer_tree
    from Optimization.runschema import contract as rcontract
    from Optimization.runschema.resolver import RunTree

    old_doc = rcontract.load('e69b6d392929')
    assert old_doc is not None, 'the 2026-07-29 vintage document left the committed store'
    assert 'whatif_volume_csv' not in old_doc['artifacts'], (
        'this vintage now declares whatif_volume_csv — pick an older one to keep this test real')

    base = str(tmp_path / 'comparison_whatif_old_vintage')
    os.makedirs(base)
    rt = RunTree(base, old_doc, layout={})
    wt = _writer_tree(rt)
    assert wt.path('whatif_volume_csv') == os.path.join(base, 'whatif_volume.csv')
    assert wt.path('whatif_volume_json') == os.path.join(base, 'whatif_volume.json')
    assert os.path.dirname(wt.path('whatif_labor_pngs')) == base

    # A run whose contract already declares the artifacts keeps ITS OWN resolver untouched.
    current = resolver_for(_mixed_tree(tmp_path))
    assert _writer_tree(current) is current


# ── the run dossier (run-scope evaluations) ──────────────────────────────────────
# Replaces the retired `_runtime/` pin: that writer drew pre-chartkit absolute-second bars
# and its output was never staged anywhere, so nothing depended on the literal it produced.

def test_dossier_tree_renders_on_both_run_shapes(tmp_path):
    """The dossier is a RUN-scope tree, so it must render identically whether the run has a
    channel level or not — the store-only shape is where a level-counting join goes wrong."""
    for base in (_mixed_tree(tmp_path), _store_only_tree(tmp_path)):
        rt = resolver_for(base)
        root = os.path.join(base, '_dossier')
        assert rt.dossier_dir() == root
        assert os.path.dirname(rt.path('dossier_cost_pngs')) == \
            os.path.join(root, 'figures', 'cost')
        assert os.path.dirname(rt.path('dossier_tables_csv')) == os.path.join(root, 'tables')
        for name in ('dossier_json', 'rule_catalog_json', 'inventory_model_json',
                     'held_fixed_json', 'comparison_census_json'):
            assert os.path.dirname(rt.path(name)) == root, name


def test_dossier_documents_are_grouped_so_staging_needs_no_name_list(tmp_path):
    """Every dossier artifact carries the group tag ingest stages by.

    The rollup and leaf-table stages name their artifacts one at a time, which is why each
    new one costs an ingest edit.  The dossier follows the what-if pattern instead: a group
    tag, so the seventh document stages itself."""
    rt = resolver_for(_mixed_tree(tmp_path))
    tagged = {n for n, a in rt.artifacts.items() if a.get('group') == 'dossier'}
    assert 'dossier_json' in tagged and 'comparison_census_json' in tagged
    assert 'dossier_dir' not in tagged, 'the stage root is not a document to stage'
