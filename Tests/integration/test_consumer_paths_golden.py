"""test_consumer_paths_golden.py — the consumer migration onto the run-tree contract is a NO-OP.

The VISUALIZATION / DIAGNOSTICS / SCRIPTS / DOCS consumers used to hand-join run-tree paths;
they now resolve through the run's own contract (``runschema.resolver_for``) when the run has a
descriptor, and keep the old hand-join as the documented fallback when it does not.  This file
is the proof that the migration changed NOTHING: every golden below is hardcoded from the OLD
logic (a literal join, a splitext, a raw glob, an os.walk), and the contract route must produce
the identical path or file-set on both tree shapes (mixed catalog with a ``<channel>`` level,
store-only without one) plus the pre-descriptor legacy shape.

Covered consumers:

  * ``Visualization/db_reader.py``  — viz_cache_path, keyframe sidecar, _nearest_warehouse_db
  * ``Diagnostics/replay_run.py``   — discover_sim_dbs, _nearest_warehouse_db
  * ``scripts/archive_cells.py``    — _in_flight (resume.pkl / _ckpt_*.pkl from the contract)
  * ``docs/experiments/ingest.py``  — _leaf_file, _stage_whatif, _stage_inventory_assets,
                                      _repo_provenance_of, _cell_inventory_configs
  * ``docs/macros.py``              — _verify_manifest_schema (additive; silent w/o schema_id)

Filesystem-only — fake trees in tmp_path, no simulation, no SQLite reads.

    python -m pytest Tests/integration/test_consumer_paths_golden.py -q
"""
from __future__ import annotations

import copy
import glob as _glob
import importlib.util
import os
import sys

import pytest

from Optimization import runschema
from Optimization.runschema.resolver import RunTree
from Optimization.runschema.sim_manifest import write_run_layout

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The archiver and the docs ingest are entry scripts, not packages — import them the way their
# sibling tests do (archive_cells via sys.path, ingest/macros by file location).
sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
import archive_cells as ac                                                  # noqa: E402


def _load_by_path(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, *rel.split('/')))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ingest = _load_by_path('_ingest_under_test', 'docs/experiments/ingest.py')
replay = _load_by_path('_replay_under_test', 'Diagnostics/replay_run.py')


# ── fixture trees ────────────────────────────────────────────────────────────────

def _touch(*parts):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('{}')
    return path


def _write_layout(base, **over):
    kw = dict(spec='_canary_single', reference='k1_off',
              cells=[('k1_off', None, {'enabled': False}, 'round_robin')],
              pairs=[('pairA', 'i.db', 'a.db')], store_cfgs=[{'name': 'store'}],
              ff_cfgs=[], channels=['store'], arms=None, created='2026-07-28T10:00:00')
    kw.update(over)
    write_run_layout(str(base), **kw)


@pytest.fixture
def mixed(tmp_path):
    """Contract run, MIXED catalog: sim DBs one <channel>/ below <config>/."""
    base = tmp_path / 'comparison_mixed'
    base.mkdir()
    _write_layout(base, channels=['store', 'fulfillment'],
                  ff_cfgs=[{'name': 'ful_calibrated'}])
    b = str(base)
    _touch(b, 'k1_off', 'pairA', 'warehouse.db')
    _touch(b, 'k1_off', 'pairA', 'store', 'config.json')
    _touch(b, 'k1_off', 'pairA', 'store', 'store', 'sim_uni_fifo_norsl.db')
    _touch(b, 'k1_off', 'pairA', 'store', 'store', 'sim_uni_fifo_norsl.keyframes.db')
    _touch(b, 'k1_off', 'pairA', 'store', 'store', 'sim_meta.json')
    _touch(b, 'k1_off', 'pairA', 'ful_calibrated', 'fulfillment', 'sim_opt_map_norsl.db')
    _touch(b, 'k1_off', 'pairA', 'ful_calibrated', 'fulfillment', 'sim_meta.json')
    return b, runschema.resolver_for(b)


@pytest.fixture
def store_only(tmp_path):
    """Contract run, STORE-ONLY catalog: sim DBs directly under <config>/ — no channel level."""
    base = tmp_path / 'comparison_store_only'
    base.mkdir()
    _write_layout(base)
    b = str(base)
    _touch(b, 'k1_off', 'pairA', 'warehouse.db')
    _touch(b, 'k1_off', 'pairA', 'store', 'config.json')
    _touch(b, 'k1_off', 'pairA', 'store', 'sim_uni_fifo_norsl.db')
    _touch(b, 'k1_off', 'pairA', 'store', 'sim_uni_fifo_norsl.keyframes.db')
    _touch(b, 'k1_off', 'pairA', 'store', 'sim_meta.json')
    return b, runschema.resolver_for(b)


@pytest.fixture
def legacy(tmp_path):
    """Pre-descriptor archive: flat <pair>/<config>/sim_*.db, no run_layout.json at all."""
    base = tmp_path / 'legacy_flat'
    b = str(base)
    _touch(b, 'pairA', 'warehouse.db')
    _touch(b, 'pairA', 'calibrated', 'sim_uni_fifo_norsl.db')
    _touch(b, 'pairA', 'calibrated', 'sim_uni_fifo_norsl.keyframes.db')
    _touch(b, 'pairA', 'calibrated', 'sim_opt_map_norsl.db')
    return b


# ── Visualization/db_reader.py ───────────────────────────────────────────────────

def test_viz_cache_path_contract_equals_hand_join(mixed, store_only):
    from Visualization.db_reader import viz_cache_path
    b, rt = mixed
    golden = os.path.join(b, '_viz', 'k1_off', 'pairA', 'store', 'store',
                          'uni_fifo_norsl.viz.db')                    # the OLD hand-join, verbatim
    assert viz_cache_path(b, 'k1_off', 'pairA', 'store', 'store', 'uni_fifo_norsl', rt=rt) == golden
    assert viz_cache_path(b, 'k1_off', 'pairA', 'store', 'store', 'uni_fifo_norsl') == golden

    bs, rts = store_only
    golden_s = os.path.join(bs, '_viz', 'k1_off', 'pairA', 'store', 'uni_fifo_norsl.viz.db')
    assert viz_cache_path(bs, 'k1_off', 'pairA', 'store', None, 'uni_fifo_norsl', rt=rts) == golden_s
    assert viz_cache_path(bs, 'k1_off', 'pairA', 'store', None, 'uni_fifo_norsl') == golden_s


def test_viz_cache_path_survives_a_contract_predating_the_artifact(mixed):
    """The whole archive predates `viz_cache_db` (added in 0516f5aab255).  A resolver bound to
    such a contract must fall back to the hand-join, not KeyError the viewer."""
    from Visualization.db_reader import viz_cache_path
    b, rt = mixed
    doc = copy.deepcopy(rt.contract)
    del doc['artifacts']['viz_cache_db']
    old_rt = RunTree(b, doc, layout=rt.layout)
    golden = os.path.join(b, '_viz', 'k1_off', 'pairA', 'store', 'store', 'uni_fifo_norsl.viz.db')
    assert viz_cache_path(b, 'k1_off', 'pairA', 'store', 'store', 'uni_fifo_norsl',
                          rt=old_rt) == golden


def test_keyframe_sidecar_contract_equals_splitext(mixed, store_only):
    for b, rt in (mixed, store_only):
        for _cell, _cr, sim_db in rt.sim_dbs():
            golden = os.path.splitext(sim_db)[0] + '.keyframes.db'    # the OLD derivation
            assert rt.keyframe_db(sim_db) == golden


def test_nearest_warehouse_db_contract_equals_walk_up(mixed, store_only, legacy):
    from Visualization.db_reader import _nearest_warehouse_db
    for b, rt in (mixed, store_only):
        golden = os.path.join(b, 'k1_off', 'pairA', 'warehouse.db')
        for _cell, cr, sim_db in rt.sim_dbs():
            assert _nearest_warehouse_db(sim_db, rt=rt, cell='k1_off', pair=cr.pair) == golden
            assert _nearest_warehouse_db(sim_db) == golden            # the OLD walk-up, unchanged

    sim = os.path.join(legacy, 'pairA', 'calibrated', 'sim_uni_fifo_norsl.db')
    assert _nearest_warehouse_db(sim) == os.path.join(legacy, 'pairA', 'warehouse.db')


# ── Diagnostics/replay_run.py ────────────────────────────────────────────────────

def test_replay_discovery_contract_equals_raw_glob(mixed, store_only):
    for b, rt in (mixed, store_only):
        # The OLD logic, verbatim: recursive glob minus the keyframe sidecars, sorted.
        golden = sorted(p for p in _glob.glob(os.path.join(b, '**', 'sim_*.db'), recursive=True)
                        if not p.endswith('.keyframes.db'))
        assert golden, 'fixture produced no sim DBs — the comparison would be vacuous'
        assert replay.discover_sim_dbs(b, rt=rt) == golden
        assert replay._contract_resolver(b) is not None

    b, _rt = mixed
    golden_names = [os.path.join('k1_off', 'pairA', 'ful_calibrated', 'fulfillment',
                                 'sim_opt_map_norsl.db'),
                    os.path.join('k1_off', 'pairA', 'store', 'store', 'sim_uni_fifo_norsl.db')]
    assert replay.discover_sim_dbs(b, rt=replay._contract_resolver(b)) == [
        os.path.join(b, rel) for rel in golden_names]


def test_replay_discovery_falls_back_on_a_pre_descriptor_archive(legacy):
    assert replay._contract_resolver(legacy) is None
    golden = [os.path.join(legacy, 'pairA', 'calibrated', 'sim_opt_map_norsl.db'),
              os.path.join(legacy, 'pairA', 'calibrated', 'sim_uni_fifo_norsl.db')]
    assert replay.discover_sim_dbs(legacy) == golden
    # A single-DB target bypasses discovery entirely, resolver or not.
    assert replay.discover_sim_dbs(golden[0]) == [golden[0]]


def test_replay_nearest_warehouse_db_positional_parts(mixed, store_only, legacy):
    """The replay copy resolves cell/pair POSITIONALLY from the leaf — the `<pair>/store/store/`
    collision is exactly why it must never match directory names."""
    for b, rt in (mixed, store_only):
        golden = os.path.join(b, 'k1_off', 'pairA', 'warehouse.db')
        for _cell, _cr, sim_db in rt.sim_dbs():
            assert replay._nearest_warehouse_db(sim_db, rt=rt) == golden
            assert replay._nearest_warehouse_db(sim_db) == golden     # the OLD walk-up, unchanged
    sim = os.path.join(legacy, 'pairA', 'calibrated', 'sim_uni_fifo_norsl.db')
    assert replay._nearest_warehouse_db(sim) == os.path.join(legacy, 'pairA', 'warehouse.db')


# ── scripts/archive_cells.py ─────────────────────────────────────────────────────

def _old_in_flight(cell_dir):
    """The retired hardcoded walk, transcribed verbatim — the golden for _in_flight."""
    out = []
    for root, _dirs, files in os.walk(cell_dir):
        for fn in files:
            if fn == 'resume.pkl' or (fn.startswith('_ckpt_') and fn.endswith('.pkl')):
                out.append(os.path.relpath(os.path.join(root, fn), cell_dir))
    return out


def test_in_flight_contract_equals_hardcoded_walk(mixed, store_only):
    b, rt = mixed
    _touch(b, 'k1_off', 'pairA', 'store', 'store', 'resume.pkl')
    _touch(b, 'k1_off', 'pairA', 'ful_calibrated', 'fulfillment', '_ckpt_opt_map_norsl.pkl')
    golden = sorted([os.path.join('pairA', 'ful_calibrated', 'fulfillment',
                                  '_ckpt_opt_map_norsl.pkl'),
                     os.path.join('pairA', 'store', 'store', 'resume.pkl')])
    assert ac._in_flight(rt, 'k1_off') == golden
    assert sorted(_old_in_flight(rt.cell_dir('k1_off'))) == golden    # both routes, same set

    bs, rts = store_only
    _touch(bs, 'k1_off', 'pairA', 'store', 'resume.pkl')
    _touch(bs, 'k1_off', 'pairA', 'store', '_ckpt_uni_fifo_norsl.pkl')
    golden_s = sorted([os.path.join('pairA', 'store', '_ckpt_uni_fifo_norsl.pkl'),
                       os.path.join('pairA', 'store', 'resume.pkl')])
    assert ac._in_flight(rts, 'k1_off') == golden_s
    assert sorted(_old_in_flight(rts.cell_dir('k1_off'))) == golden_s


def test_in_flight_is_empty_on_a_finalized_cell(mixed):
    b, rt = mixed
    assert ac._in_flight(rt, 'k1_off') == []


# ── docs/experiments/ingest.py ───────────────────────────────────────────────────

def _stage_figures(b):
    """Curated-figure layout as run_analysis writes it post-redesign: one folder per chart
    family, view-prefixed basenames unique across the whole leaf by construction."""
    leaf = (b, 'k1_off', 'pairA', 'store', 'store')
    _touch(*leaf, 'figures', 'labor', 'delta_prodtime_top3_by_initial.png')
    _touch(*leaf, 'figures', 'headline', 'percent_top_vs_baseline.png')
    _touch(*leaf, 'figures', 'trajectories', 'absolute_production_time.png')
    _touch(*leaf, 'figures', 'task_time', 'absolute_task_duration_ranked.png')
    _touch(*leaf, 'figures', 'significance', 'effect_heatmap.png')


def test_ingest_leaf_file_contract_equals_walk(mixed):
    b, rt = mixed
    _stage_figures(b)
    cfg_src = os.path.join(b, 'k1_off', 'pairA', 'store')
    leaf = os.path.join(cfg_src, 'store')
    goldens = {
        'config.json': os.path.join(cfg_src, 'config.json'),
        'delta_prodtime_top3_by_initial.png':
            os.path.join(leaf, 'figures', 'labor', 'delta_prodtime_top3_by_initial.png'),
        'percent_top_vs_baseline.png':
            os.path.join(leaf, 'figures', 'headline', 'percent_top_vs_baseline.png'),
        'absolute_production_time.png':
            os.path.join(leaf, 'figures', 'trajectories', 'absolute_production_time.png'),
        'effect_heatmap.png':
            os.path.join(leaf, 'figures', 'significance', 'effect_heatmap.png'),
    }
    for name, golden in goldens.items():
        assert ingest._find(cfg_src, name) == golden, name            # the OLD walk, unchanged
        assert ingest._leaf_file(rt, 'k1_off', 'pairA', 'store', name, cfg_src) == golden, name
    # A figure the run never produced: both routes say None, and _copy logs MISSING.
    assert ingest._leaf_file(rt, 'k1_off', 'pairA', 'store', 'nope.png', cfg_src) is None
    assert ingest._find(cfg_src, 'nope.png') is None


def test_ingest_leaf_file_store_only_shape(store_only):
    b, rt = store_only
    _touch(b, 'k1_off', 'pairA', 'store', 'figures', 'headline', 'percent_top_vs_baseline.png')
    cfg_src = os.path.join(b, 'k1_off', 'pairA', 'store')
    golden = os.path.join(cfg_src, 'figures', 'headline', 'percent_top_vs_baseline.png')
    assert ingest._find(cfg_src, 'percent_top_vs_baseline.png') == golden
    assert ingest._leaf_file(rt, 'k1_off', 'pairA', 'store',
                             'percent_top_vs_baseline.png', cfg_src) == golden
    assert ingest._leaf_file(rt, 'k1_off', 'pairA', 'store',
                             'config.json', cfg_src) == os.path.join(cfg_src, 'config.json')


def test_ingest_run_spec_and_manifest_literals(mixed):
    b, rt = mixed
    assert rt.run_spec_json() == os.path.join(b, 'run_spec.json')             # old literal join
    assert rt.run_manifest('k1_off') == os.path.join(b, 'k1_off', 'run_manifest.json')

    log = []
    _touch(b, 'run_spec.json')  # empty {} — no repo_commit key
    assert ingest._repo_provenance_of(b, log, rt) == ('unknown', None)

    rm = {'inventories': ['pairA'], 'configs': [{'name': 'store'}], 'run': 'x'}
    import json as _json
    with open(os.path.join(b, 'k1_off', 'run_manifest.json'), 'w', encoding='utf-8') as fh:
        _json.dump(rm, fh)
    got_rm, invs, cfgs = ingest._cell_inventory_configs(
        os.path.join(b, 'k1_off'), b, {}, log, rt=rt, cell='k1_off')
    assert (invs, cfgs) == (['pairA'], ['store'])
    assert got_rm == rm


def test_ingest_sim_meta_lookup_contract_equals_walk(mixed, store_only):
    for b, rt in (mixed, store_only):
        inv_src = os.path.join(b, 'k1_off', 'pairA')
        golden = ingest._find(inv_src, 'sim_meta.json')               # the OLD walk's pick
        assert golden is not None
        metas = rt.glob('sim_meta', cell='k1_off', pair='pairA')
        assert metas and metas[0] == golden


def test_ingest_whatif_staging_contract_equals_glob(mixed, tmp_path):
    b, rt = mixed
    for fname in ('whatif_delta.json', 'whatif_delta.csv', 'whatif_delta.png',
                  'whatif_batch_hours_rr_vs_lpt.png', 'whatif_volume_curves.png'):
        _touch(b, fname)

    # One shared destination: dry runs copy nothing, so both routes may log against the SAME
    # exp_dir — which makes the two logs directly comparable, line for line.
    exp = str(tmp_path / 'exp')
    log_old, log_new = [], []
    n_old = ingest._stage_whatif(b, exp, True, log_old)               # dry: the OLD literal route
    n_new = ingest._stage_whatif(b, exp, True, log_new, rt=rt)        # dry: the contract route
    assert n_old == n_new == 4                                        # delta.json + 3 pngs, no csv
    assert log_old == log_new, 'the contract route staged a different (src -> dst) sequence'

    # Golden, from the old logic: whatif_delta.json to data/ (the csv is NOT in
    # DEFAULT_WHATIF_DATA), then every whatif_*.png to images/, in sorted order.
    def line(src, *dst):
        return f"  copy     {src}  ->  {os.path.relpath(os.path.join(exp, *dst), ingest._DOCS)}"
    assert log_old == [
        line(os.path.join(b, 'whatif_delta.json'), 'data', 'whatif_delta.json'),
        line(os.path.join(b, 'whatif_batch_hours_rr_vs_lpt.png'),
             'images', 'whatif_batch_hours_rr_vs_lpt.png'),
        line(os.path.join(b, 'whatif_delta.png'), 'images', 'whatif_delta.png'),
        line(os.path.join(b, 'whatif_volume_curves.png'), 'images', 'whatif_volume_curves.png'),
    ]


# ── docs/macros.py ───────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def macros():
    pytest.importorskip('yaml', reason='docs/macros.py imports yaml at module scope')
    return _load_by_path('_macros_under_test', 'docs/macros.py')


def test_macros_verifier_is_silent_without_schema_id(macros):
    macros._verify_manifest_schema({'run': 'x'}, os.path.join(_ROOT, 'docs'), 'experiments/e')
    macros._verify_manifest_schema({'schema_id': None}, os.path.join(_ROOT, 'docs'),
                                   'experiments/e')


def test_macros_verifier_accepts_every_committed_contract(macros):
    """All committed run-tree contracts share the cell/pair/config(+channel?) levels today, so a
    manifest recording ANY of them must pass — including the head a fresh ingest would stamp."""
    from Optimization.runschema import contract
    docs_dir = os.path.join(_ROOT, 'docs')
    ids = list(contract.load_all())
    assert contract.head() in ids and len(ids) >= 1
    for sid in ids:
        macros._verify_manifest_schema({'schema_id': sid}, docs_dir, 'experiments/e')


def test_macros_verifier_rejects_an_uncommitted_schema(macros):
    sid = 'sha256:' + 'de1e7ed'.ljust(64, '0')
    with pytest.raises(FileNotFoundError) as ei:
        macros._verify_manifest_schema({'schema_id': sid}, os.path.join(_ROOT, 'docs'),
                                       'experiments/e')
    assert sid in str(ei.value)


def test_macros_verifier_rejects_moved_levels(macros, tmp_path):
    """A contract whose levels no longer read cell/pair/config(+channel?) must fail the build
    LOUDLY, naming the schema id — that is the entire point of recording it."""
    import json as _json
    sid = 'sha256:' + 'ab5e12'.ljust(64, '0')
    short = sid.split(':', 1)[-1][:12]
    store = tmp_path / 'Optimization' / 'schemas' / 'run_tree'
    store.mkdir(parents=True)
    docs_dir = tmp_path / 'docs'
    docs_dir.mkdir()
    (store / f'{short}.json').write_text(_json.dumps({
        'schema_id': sid,
        'levels': [{'name': 'cell', 'optional': False},
                   {'name': 'regime', 'optional': False},      # a level the macros know nothing of
                   {'name': 'pair', 'optional': False},
                   {'name': 'config', 'optional': False},
                   {'name': 'channel', 'optional': True}],
    }), encoding='utf-8')
    with pytest.raises(ValueError) as ei:
        macros._verify_manifest_schema({'schema_id': sid}, str(docs_dir), 'experiments/e')
    assert sid in str(ei.value) and 'regime' in str(ei.value)
