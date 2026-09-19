"""test_unload_ranking.py — phase 2's hand-off ranks CELLS on total site labour, ties inside
the declared floor go to yard overage, and it never ranks on nothing.

`run_unload_ranking` is the seam between the funnel's phases 2 and 3: it reads a coupled
phase-2 run's series documents (labour) and site yard DBs (overage), ranks the unloading
policies -- the cells -- per rule pair, and writes `unload_ranking.json` naming the cells
phase 3 will cross with phase 1's best three.  The decisions with a silent failure mode:

  * **The score is a composite with a declared floor.**  Labour decides outside the floor;
    inside it the yard overage decides.  A tie is not transitive, so the grouping rule is
    stated (walk the labour order, join the open group while within the floor of ITS
    cheapest) and pinned here on a chain of near-ties.
  * **A unit is an ARM PAIR from the site DB's name**, mapped to a rule pair through the
    strategy grid; a cell's score for a pair sums both stock modes.
  * **Absence is loud.**  A unit missing a labour reading poisons its cell for that pair
    (rank None); an uncoupled root, an unanalyzed root and a one-cell root each refuse.

The on-disk tests build a JSON scratch tree like `test_restock_selection.py`'s and patch the
two site readers (`_site_units`, `_unit_overage`), because a site yard DB is a simulation
output no unit test should have to mint.

Run:  python -m pytest Tests/unit/test_unload_ranking.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from Optimization import run_unload_ranking as ur


# ── the pure part ────────────────────────────────────────────────────────────────────

def _e(cell, labor, overage):
    return {'cell': cell, 'labor_hours': labor, 'overage_days': overage}


def test_labour_decides_outside_the_floor_even_against_a_worse_overage():
    ranked = ur.rank_cells([_e('a', 100.0, 9.0), _e('b', 101.0, 0.0)], noise_floor_pct=0.1)
    assert [r['cell'] for r in ranked] == ['a', 'b']
    assert ranked[0]['decided_by'] == 'labour' and ranked[0]['tie_group'] != ranked[1]['tie_group']
    assert ranked[0]['margin_pct'] == pytest.approx(1.0)
    assert ranked[1]['margin_pct'] is None


def test_overage_decides_inside_the_floor():
    ranked = ur.rank_cells([_e('a', 100.0, 9.0), _e('b', 100.05, 0.0)], noise_floor_pct=0.1)
    assert [r['cell'] for r in ranked] == ['b', 'a']
    assert all(r['decided_by'] == 'overage' and r['tie_group'] == 1 for r in ranked)
    assert ranked[0]['rank'] == 1 and ranked[1]['rank'] == 2


def test_a_chain_of_near_ties_groups_against_the_open_groups_cheapest():
    """a~b and b~c but NOT a~c: the group is anchored on the cheapest member, so c opens a
    new group and is ranked on labour against the whole first group."""
    ranked = ur.rank_cells([_e('a', 100.0, 5.0), _e('b', 100.08, 1.0), _e('c', 100.16, 0.0)],
                           noise_floor_pct=0.1)
    assert [r['cell'] for r in ranked] == ['b', 'a', 'c']
    assert [r['tie_group'] for r in ranked] == [1, 1, 2]
    assert [r['decided_by'] for r in ranked] == ['overage', 'overage', 'labour']


def test_a_missing_labour_reading_ranks_last_with_no_rank():
    ranked = ur.rank_cells([_e('a', None, 0.0), _e('b', 50.0, 0.0), _e('c', float('nan'), 0.0)],
                           noise_floor_pct=0.1)
    assert [r['cell'] for r in ranked] == ['b', 'a', 'c']
    assert ranked[0]['rank'] == 1 and ranked[1]['rank'] is None and ranked[2]['rank'] is None
    assert ranked[1]['margin_pct'] is None and ranked[1]['decided_by'] is None


def test_rank_cells_returns_new_dicts_and_leaves_the_input_alone():
    src = [_e('a', 1.0, 0.0)]
    out = ur.rank_cells(src, 0.1)
    assert out[0] is not src[0] and 'rank' not in src[0]


# ── on disk: a coupled two-cell phase-2 root, site readers patched ───────────────────

def _leaf(root, cell, pair, config, channel, arms):
    d = os.path.join(root, cell, pair, config, channel)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'sim_meta.json'), 'w') as f:
        json.dump({'name': config, 'run_dir': d, 'inventory': pair, 'channel': channel,
                   'strategies': [{'key': k} for k in arms]}, f)
    with open(os.path.join(d, 'series.json'), 'w') as f:
        json.dump({'strategies': [{'key': k, ur.METRIC_FIELD: v} for k, v in arms.items()]}, f)


def _layout(root, cells, threshold=0.4):
    from Optimization.runschema import contract
    doc = {'version': 3, 'schema_id': contract.head(), 'kind': 'sweep',
           'spec': 'inbound_unload', 'base': os.path.basename(root), 'reference': cells[0],
           'cells': [{'name': c, 'inbound': {'yard_policy': c.rsplit('_', 1)[-1],
                                             'fee_threshold_days': threshold,
                                             'standing_yard': True}} for c in cells],
           'pairs': ['prof_a'], 'channels': ['store', 'fulfillment'], 'coupled': True,
           'configs': {'store': ['store'], 'fulfillment': ['ful']}, 'arms': None}
    with open(os.path.join(root, 'run_layout.json'), 'w') as f:
        json.dump(doc, f)


#: The winner pair (rank_cartlabor / rank_minlabor) and the rider, both stock modes.
_UNITS = [('uni_rank_cartlabor_norsl__uni_rank_minlabor_norsl',
           {'store': 'uni_rank_cartlabor_norsl', 'fulfillment': 'uni_rank_minlabor_norsl'}),
          ('opt_rank_cartlabor_norsl__opt_rank_minlabor_norsl',
           {'store': 'opt_rank_cartlabor_norsl', 'fulfillment': 'opt_rank_minlabor_norsl'}),
          ('uni_fifo_norsl__uni_fifo_norsl',
           {'store': 'uni_fifo_norsl', 'fulfillment': 'uni_fifo_norsl'}),
          ('opt_fifo_norsl__opt_fifo_norsl',
           {'store': 'opt_fifo_norsl', 'fulfillment': 'opt_fifo_norsl'})]


def _root(tmp_path, cells: dict, *, layout=True):
    """`cells` = {cell: {'store': {arm: seconds}, 'fulfillment': {arm: seconds}}}."""
    root = str(tmp_path / 'comparison_whatif_20260919_000000')
    os.makedirs(root, exist_ok=True)
    for cell, by_ch in cells.items():
        _leaf(root, cell, 'prof_a', 'store', 'store', by_ch['store'])
        _leaf(root, cell, 'prof_a', 'ful', 'fulfillment', by_ch['fulfillment'])
    if layout:
        _layout(root, list(cells))
    return root


def _patch_site(monkeypatch, overage_by_cell_unit: dict, coupled=True):
    """Stand in for the site DBs: every cell carries the four units, overage per (cell, unit)."""
    def units(rt, cell, pair, have):
        return [(ap, dict(halves), f'{cell}/{pair}/_site/inbound_{ap}.db')
                for ap, halves in _UNITS] if coupled else []

    def overage(rt, runs_by_channel, halves, site_db, threshold_days, log):
        cell = site_db.split('/')[0]
        ap = os.path.basename(site_db)[len('inbound_'):-len('.db')]
        return overage_by_cell_unit.get((cell, ap), 0.0)
    monkeypatch.setattr(ur, '_site_units', units)
    monkeypatch.setattr(ur, '_unit_overage', overage)


def _arms(store_secs: float, ful_secs: float, fifo_secs: float) -> dict:
    return {'store': {'uni_rank_cartlabor_norsl': store_secs, 'opt_rank_cartlabor_norsl': store_secs,
                      'uni_fifo_norsl': fifo_secs, 'opt_fifo_norsl': fifo_secs},
            'fulfillment': {'uni_rank_minlabor_norsl': ful_secs, 'opt_rank_minlabor_norsl': ful_secs,
                            'uni_fifo_norsl': fifo_secs, 'opt_fifo_norsl': fifo_secs}}


def test_cells_are_ranked_per_rule_pair_on_summed_unit_hours(tmp_path, monkeypatch):
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})
    doc = ur.rank(root, log=lambda *_a: None)
    win, rider = 'rank_cartlabor/rank_minlabor', 'fifo/fifo'
    assert doc['primary_pair'] == win and doc['rider'] == rider
    # winner pair: two units of (1 h + 1 h) = 4 h in fifo, 4.028 h in lifo -> fifo first
    w = doc['rankings'][win]
    assert [r['cell'] for r in w] == ['k1_off_fifo', 'k1_off_lifo']
    assert w[0]['labor_hours'] == pytest.approx(4.0) and w[0]['n_units'] == 2
    assert w[0]['decided_by'] == 'labour'
    # the rider ranks the OTHER way, and is reported beside, never merged
    assert [r['cell'] for r in doc['rankings'][rider]] == ['k1_off_lifo', 'k1_off_fifo']
    assert doc['chosen'][win] == ['k1_off_fifo', 'k1_off_lifo']
    assert doc['metric']['noise_floor_pct'] == ur.DEFAULT_NOISE_FLOOR_PCT
    assert os.path.exists(os.path.join(root, 'unload_ranking.json'))
    with open(os.path.join(root, 'unload_ranking.json')) as f:
        assert json.load(f)['chosen'] == doc['chosen']


def test_inside_the_floor_the_site_overage_decides_the_cell(tmp_path, monkeypatch):
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_gmyopic': _arms(3601.0, 3600.0, 7200.0)})   # 0.014% apart
    ap_uni, ap_opt = _UNITS[0][0], _UNITS[1][0]
    _patch_site(monkeypatch, {('k1_off_fifo', ap_uni): 2.0, ('k1_off_fifo', ap_opt): 1.5,
                              ('k1_off_gmyopic', ap_uni): 0.5, ('k1_off_gmyopic', ap_opt): 0.0})
    doc = ur.rank(root, log=lambda *_a: None)
    w = doc['rankings'][doc['primary_pair']]
    assert [r['cell'] for r in w] == ['k1_off_gmyopic', 'k1_off_fifo']
    assert w[0]['decided_by'] == 'overage' and w[0]['overage_days'] == pytest.approx(0.5)
    assert w[1]['overage_days'] == pytest.approx(3.5)


def test_a_unit_with_a_missing_reading_poisons_its_cell_for_that_pair(tmp_path, monkeypatch):
    cells = {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
             'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)}
    cells['k1_off_lifo']['store'].pop('opt_rank_cartlabor_norsl')      # one leaf lost an arm
    root = _root(tmp_path, cells)
    _patch_site(monkeypatch, {})
    doc = ur.rank(root, log=lambda *_a: None)
    w = {r['cell']: r for r in doc['rankings'][doc['primary_pair']]}
    assert w['k1_off_lifo']['rank'] is None and w['k1_off_lifo']['labor_hours'] is None
    assert w['k1_off_fifo']['rank'] == 1
    assert doc['rankings']['fifo/fifo'][0]['rank'] == 1          # the rider is untouched


def test_an_uncoupled_root_is_refused(tmp_path, monkeypatch):
    root = _root(tmp_path, {'k1_off_fifo': _arms(1.0, 1.0, 1.0), 'k1_off_lifo': _arms(1.0, 1.0, 1.0)})
    _patch_site(monkeypatch, {}, coupled=False)
    with pytest.raises(SystemExit, match='UNCOUPLED'):
        ur.rank(root, log=lambda *_a: None)


def test_a_single_cell_is_refused(tmp_path, monkeypatch):
    root = _root(tmp_path, {'k1_off_fifo': _arms(1.0, 1.0, 1.0)})
    _patch_site(monkeypatch, {})
    with pytest.raises(SystemExit, match='at least'):
        ur.rank(root, log=lambda *_a: None)


def test_an_unanalyzed_root_says_what_to_run(tmp_path, monkeypatch):
    root = _root(tmp_path, {'k1_off_fifo': _arms(1.0, 1.0, 1.0), 'k1_off_lifo': _arms(1.0, 1.0, 1.0)})
    for cell in ('k1_off_fifo', 'k1_off_lifo'):
        for cfg, ch in (('store', 'store'), ('ful', 'fulfillment')):
            os.remove(os.path.join(root, cell, 'prof_a', cfg, ch, 'series.json'))
    _patch_site(monkeypatch, {})
    with pytest.raises(SystemExit, match='analyze_run'):
        ur.rank(root, log=lambda *_a: None)


def test_an_explicit_pair_the_run_never_carried_is_refused(tmp_path, monkeypatch):
    root = _root(tmp_path, {'k1_off_fifo': _arms(1.0, 1.0, 1.0), 'k1_off_lifo': _arms(1.0, 1.0, 1.0)})
    _patch_site(monkeypatch, {})
    with pytest.raises(SystemExit, match='ran in no cell'):
        ur.rank(root, primary_pair=('tmin', 'tmin'), log=lambda *_a: None)


def test_the_artifact_is_declared_by_the_run_tree_contract():
    from Optimization.runschema import schema
    art = schema.ARTIFACTS['unload_ranking_json'] if hasattr(schema, 'ARTIFACTS') else None
    if art is None:                       # the declaration table's name is the contract's business
        import inspect
        assert "'unload_ranking_json'" in inspect.getsource(schema)
        return
    assert art['scope'] == 'run' and art['writer'] == 'rank@Optimization/run_unload_ranking.py'
