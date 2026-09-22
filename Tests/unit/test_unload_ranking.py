"""test_unload_ranking.py — phase 2's hand-off ranks CELLS on the PLACEMENT SCORE, ties
inside the declared floor go to yard overage, and it never ranks on nothing.

`run_unload_ranking` is the seam between the funnel's phases 2 and 3: it reads a coupled
phase-2 run's series documents (the score and its census) and site yard DBs (overage),
ranks the unloading policies -- the cells -- per rule pair, and writes
`unload_ranking.json` naming the cells phase 3 will cross with phase 1's best three.

It ranked on total site labour until 2026-09-19.  That was a FLOW total, and a flow total
is invariant to the order trailers come apart in over a window long enough to unload
everything, so the first phase-2 ranking was an exact tie across ten cells.  The score is
now `ss_pick_owed`: what the run's planned batches would cost served from where the stock
currently stands.  The decisions with a silent failure mode:

  * **The score is a composite with a floor.**  The score decides outside the floor;
    inside it the yard overage decides.  A tie is not transitive, so the grouping rule is
    stated (walk the score order, join the open group while within the floor of ITS
    cheapest) and pinned here on a chain of near-ties.  Since 2026-09-22 the floor is
    MEASURED per rule pair from the paired per-batch series (`measured_floor`) and the
    declared 0.1% is the fallback; the score that ranks is the census-ADJUSTED one
    (`adjusted_owed`); and the rider is a CONTROL that `rank_agreement` sets aside
    (`control_verdict` reads it).  The last block of tests pins those three.
  * **A unit is an ARM PAIR from the site DB's name**, mapped to a rule pair through the
    strategy grid; a cell's score for a pair sums both stock modes.
  * **The census can veto the ranking.**  A planned line with no shelf stock costs the
    score nothing, so a run with material unservable demand would rank "leave it in the
    yard" first.  The tool writes its record, names NO cells and reports the refusal.
  * **Two verdicts a rank list cannot state.**  `discriminating` is false when every cell
    lands in one tie group; `rank_agreement` is false when the rule pairs -- independent
    replications of the same question -- order the cells differently.
  * **Absence is loud.**  A unit missing a score reading poisons its cell for that pair
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

def _e(cell, owed, overage):
    return {'cell': cell, 'owed_seconds': owed, 'overage_days': overage}


def test_the_score_decides_outside_the_floor_even_against_a_worse_overage():
    ranked = ur.rank_cells([_e('a', 100.0, 9.0), _e('b', 101.0, 0.0)], noise_floor_pct=0.1)
    assert [r['cell'] for r in ranked] == ['a', 'b']
    assert ranked[0]['decided_by'] == 'score' and ranked[0]['tie_group'] != ranked[1]['tie_group']
    assert ranked[0]['margin_pct'] == pytest.approx(1.0)
    assert ranked[1]['margin_pct'] is None


def test_overage_decides_inside_the_floor():
    ranked = ur.rank_cells([_e('a', 100.0, 9.0), _e('b', 100.05, 0.0)], noise_floor_pct=0.1)
    assert [r['cell'] for r in ranked] == ['b', 'a']
    assert all(r['decided_by'] == 'overage' and r['tie_group'] == 1 for r in ranked)
    assert ranked[0]['rank'] == 1 and ranked[1]['rank'] == 2


def test_a_chain_of_near_ties_groups_against_the_open_groups_cheapest():
    """a~b and b~c but NOT a~c: the group is anchored on the cheapest member, so c opens a
    new group and is ranked on the score against the whole first group."""
    ranked = ur.rank_cells([_e('a', 100.0, 5.0), _e('b', 100.08, 1.0), _e('c', 100.16, 0.0)],
                           noise_floor_pct=0.1)
    assert [r['cell'] for r in ranked] == ['b', 'a', 'c']
    assert [r['tie_group'] for r in ranked] == [1, 1, 2]
    assert [r['decided_by'] for r in ranked] == ['overage', 'overage', 'score']


def test_a_missing_score_reading_ranks_last_with_no_rank():
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
        # A value may be a bare score or a (score, census) pair -- the census is read from
        # the SAME document in the same pass, so a fixture that could not carry both would
        # not be able to reach the refusal at all.
        json.dump({'strategies': [
            {'key': k, ur.METRIC_FIELD: (v[0] if isinstance(v, tuple) else v),
             ur.CENSUS_FIELD: (v[1] if isinstance(v, tuple) else 0.0)}
            for k, v in arms.items()]}, f)


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


def _arms(store_secs, ful_secs, fifo_secs) -> dict:
    """Each value is an owed-seconds reading, or a (owed_seconds, unservable) pair."""
    return {'store': {'uni_rank_cartlabor_norsl': store_secs, 'opt_rank_cartlabor_norsl': store_secs,
                      'uni_fifo_norsl': fifo_secs, 'opt_fifo_norsl': fifo_secs},
            'fulfillment': {'uni_rank_minlabor_norsl': ful_secs, 'opt_rank_minlabor_norsl': ful_secs,
                            'uni_fifo_norsl': fifo_secs, 'opt_fifo_norsl': fifo_secs}}


def test_cells_are_ranked_per_rule_pair_on_summed_unit_scores(tmp_path, monkeypatch):
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})
    doc = ur.rank(root, log=lambda *_a: None)
    win, rider = 'rank_cartlabor/rank_minlabor', 'fifo/fifo'
    assert doc['primary_pair'] == win and doc['rider'] == rider
    # winner pair: two units of (3600 + 3600) = 14,400 s in fifo, 14,500 s in lifo
    w = doc['rankings'][win]
    assert [r['cell'] for r in w] == ['k1_off_fifo', 'k1_off_lifo']
    assert w[0]['owed_seconds'] == pytest.approx(14400.0) and w[0]['n_units'] == 2
    assert w[0]['decided_by'] == 'score'
    assert doc['metric']['quantity'] == 'pick_owed_s'
    assert doc['refused'] is None and doc['discriminating'][win] is True
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
    assert w['k1_off_lifo']['rank'] is None and w['k1_off_lifo']['owed_seconds'] is None
    assert w['k1_off_fifo']['rank'] == 1
    assert doc['rankings']['fifo/fifo'][0]['rank'] == 1          # the rider is untouched


def test_a_material_census_refuses_to_name_cells_and_still_writes_the_record(
        tmp_path, monkeypatch):
    """The failure this metric could produce and no other could: a plausible ORDER.

    A planned line whose SKU has nothing on a shelf costs the score zero, so a run that
    starved would rank the policy that shelved least as best. The census is what notices,
    and the answer is a refusal rather than a fabricated penalty -- with the record still
    written, because on a run that cannot be ranked the census IS the finding.
    """
    root = _root(tmp_path, {'k1_off_fifo': _arms((3600.0, 400.0), (3600.0, 400.0), 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})
    doc = ur.rank(root, log=lambda *_a: None)
    assert doc['refused'] is not None and doc['refused']['reason'] == 'material census'
    assert doc['refused']['worst_census_pct'] == pytest.approx(100.0 * 1600.0 / 14400.0)
    assert all(v == [] for v in doc['chosen'].values()), doc['chosen']
    # the ranks themselves survive -- the refusal is about what may be HANDED ON, and a
    # reader who wants to see what the starved run ordered can still see it
    assert [r['cell'] for r in doc['rankings'][doc['primary_pair']]] == ['k1_off_fifo',
                                                                        'k1_off_lifo']
    with open(os.path.join(root, 'unload_ranking.json')) as f:
        assert json.load(f)['refused']['reason'] == 'material census'

    # NON-VACUITY: the same run with an immaterial census ranks normally, so it is the
    # SIZE of the census doing the work and not its presence.
    ok = _root(tmp_path / 'ok', {'k1_off_fifo': _arms((3600.0, 1.0), (3600.0, 1.0), 7200.0),
                                 'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    assert ur.rank(ok, log=lambda *_a: None)['refused'] is None


def test_one_tie_group_reports_not_discriminating(tmp_path, monkeypatch):
    """The 2026-09-18 result, which a rank list alone reported as a clean 1-2-3."""
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3600.0, 3600.0, 7200.0)})
    _patch_site(monkeypatch, {})
    doc = ur.rank(root, log=lambda *_a: None)
    win = doc['primary_pair']
    assert doc['discriminating'][win] is False
    assert {r['tie_group'] for r in doc['rankings'][win]} == {1}
    # and the ranks are still produced, which is exactly why the verdict has to be carried
    assert [r['rank'] for r in doc['rankings'][win]] == [1, 2]


def test_rule_pairs_that_disagree_are_reported_as_disagreeing():
    """Independent replications that order the cells differently: the order is a property
    of the rule pair, not of the unloading policy, and the document says so."""
    a = ur.rank_cells([_e('x', 100.0, 0.0), _e('y', 200.0, 0.0)], 0.1)
    b = ur.rank_cells([_e('x', 200.0, 0.0), _e('y', 100.0, 0.0)], 0.1)
    out = ur.rank_agreement({'p1': a, 'p2': b})
    assert out['agree'] is False and 'DISAGREE' in out['note']
    assert out['common'] == ['x', 'y']

    # agreement, and a single opinion, are the other two answers
    assert ur.rank_agreement({'p1': a, 'p2': a})['agree'] is True
    assert ur.rank_agreement({'p1': a})['agree'] is None

    # a pair that poisoned a cell has NO opinion about it and must not read as disagreeing
    c = ur.rank_cells([_e('x', 100.0, 0.0), _e('y', None, 0.0)], 0.1)
    assert ur.rank_agreement({'p1': a, 'p2': c})['agree'] is True


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


# ── 2026-09-22: the census is priced, the floor is measured, the rider is a control ──
# (`.scratch/inbound-throughput/issues/01`).  Each of these was a way the 2026-09-20
# campaign's ranking said something it had not measured: ~35% of the fifo-vs-gforecast gap
# was unservable lines priced at zero, the 0.1% floor was a declaration, and the rider --
# degenerate under FIFO restock by the record's own finding -- vetoed the winner pair.

def test_adjusted_owed_prices_unpriced_lines_at_the_leafs_mean_line():
    import math
    # 1,000 planned lines, 100 of them unpriced: the 900 priced cost 9,000 s (10 s a line),
    # so the census is charged 10 s a line too and the adjusted score is 10,000 s.
    assert ur.adjusted_owed(9000.0, 100.0, 1000.0) == pytest.approx(10000.0)
    # no census: unchanged, exactly
    assert ur.adjusted_owed(9000.0, 0.0, 1000.0) == 9000.0
    # nothing priceable, or no weight: NaN rather than a number
    assert math.isnan(ur.adjusted_owed(9000.0, 1000.0, 1000.0))
    assert math.isnan(ur.adjusted_owed(9000.0, 10.0, 0.0))
    assert math.isnan(ur.adjusted_owed(float('nan'), 10.0, 1000.0))


def test_planned_lines_counts_batches_per_sku_not_units():
    from types import SimpleNamespace as _B
    from Optimization.simdriver.batch_precompute import planned_lines
    batches = [_B(items={1: 5, 2: 1}), _B(items={1: 30}), _B(items={3: 2})]
    assert planned_lines(batches) == {1: 2.0, 2: 1.0, 3: 1.0}
    # the prefix the worker fielded, not the whole pickle
    assert planned_lines(batches, 2) == {1: 2.0, 2: 1.0}
    assert sum(planned_lines(batches).values()) == 4.0


def test_the_measured_floor_is_the_widest_paired_interval_against_the_reference():
    import numpy as np
    rng = np.random.default_rng(7)
    n = 40
    ref = {b: 1000.0 + 5.0 * rng.standard_normal() for b in range(n)}
    # a cell 0.5% dearer with the same noise, and one 0.5% dearer with 4x the noise
    quiet = {b: ref[b] * 1.005 + 2.0 * rng.standard_normal() for b in range(n)}
    loud = {b: ref[b] * 1.005 + 8.0 * rng.standard_normal() for b in range(n)}
    short = {b: ref[b] * 1.01 for b in range(2)}          # two shared batches: unmeasurable
    out = ur.measured_floor({'ref': ref, 'quiet': quiet, 'loud': loud, 'short': short}, 'ref')
    assert out['source'] == 'measured' and out['reference'] == 'ref'
    q, l, s = out['cells']['quiet'], out['cells']['loud'], out['cells']['short']
    assert q['n_batches'] == n and q['gap_pct'] == pytest.approx(0.5, abs=0.15)
    assert q['ci_pct'][0] < q['gap_pct'] < q['ci_pct'][1]
    half_q = (q['ci_pct'][1] - q['ci_pct'][0]) / 2
    half_l = (l['ci_pct'][1] - l['ci_pct'][0]) / 2
    assert half_l > half_q
    assert out['floor_pct'] == pytest.approx(half_l)
    assert s['gap_pct'] is None and s['ci_pct'] is None and s['n_batches'] == 2
    # the reference is never its own cell, and nothing measurable means no floor
    assert 'ref' not in out['cells']
    assert ur.measured_floor({'ref': ref, 'short': short}, 'ref')['floor_pct'] is None
    # deterministic: the bootstrap is seeded
    again = ur.measured_floor({'ref': ref, 'quiet': quiet, 'loud': loud}, 'ref')
    assert again['floor_pct'] == out['floor_pct']


def test_the_rider_is_a_control_and_never_a_replication():
    a = ur.rank_cells([_e('x', 100.0, 0.0), _e('y', 200.0, 0.0)], 0.1)
    b = ur.rank_cells([_e('x', 200.0, 0.0), _e('y', 100.0, 0.0)], 0.1)
    # the rider disagreeing with the one winner pair is NOT a disagreement any more
    out = ur.rank_agreement({'win': a, 'fifo/fifo': b}, control='fifo/fifo')
    assert out['agree'] is None and out['control'] == 'fifo/fifo'
    assert 'control' in out['note']
    # two real replications still decide it, with the control set aside
    assert ur.rank_agreement({'w1': a, 'w2': a, 'fifo/fifo': b},
                             control='fifo/fifo')['agree'] is True
    assert ur.rank_agreement({'w1': a, 'w2': b, 'fifo/fifo': a},
                             control='fifo/fifo')['agree'] is False
    # and without a control named, the old reading stands (no silent change for callers)
    assert ur.rank_agreement({'win': a, 'fifo/fifo': b})['agree'] is False

    # the control verdict: cells byte-identical to the reference on score AND overage are
    # inert under FIFO restock; the rest moved
    ranked = ur.rank_cells([_e('k1_off_fifo', 100.0, 1.0), _e('k1_off_gmyopic', 100.0, 1.0),
                            _e('k1_off_lifo', 100.0, 3.0), _e('k1_off_gforecast', 99.0, 1.0)],
                           0.1)
    v = ur.control_verdict(ranked, 'k1_off_fifo')
    assert v['inert'] == ['k1_off_gmyopic']
    assert sorted(v['moved']) == ['k1_off_gforecast', 'k1_off_lifo']
    assert 'veto' in v['note']
    # a reference with no reading leaves the control silent rather than wrong
    assert ur.control_verdict(ranked, 'k1_off_nowhere')['inert'] == []


def test_without_batch_rows_the_declared_floor_applies_and_units_say_unadjusted(
        tmp_path, monkeypatch):
    """The fixture tree has no leaf DBs and no batch pickle: the tool must rank on the raw
    scalar, apply the declared floor, and SAY so -- never quietly pretend it measured."""
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})
    doc = ur.rank(root, noise_floor_pct=0.25, log=lambda *_a: None)
    win = doc['primary_pair']
    nf = doc['noise_floor'][win]
    assert nf['applied_pct'] == 0.25 and nf['source'].startswith('declared')
    assert nf['floor_pct'] is None and nf['reference'] == 'k1_off_fifo'
    assert all(u['adjusted'] is False for u in doc['units'])
    assert all(u['owed_seconds'] == u['owed_unadjusted'] for u in doc['units'])
    assert all('by_batch' not in u for u in doc['units'])
    assert doc['rider_control']['reference'] == 'k1_off_fifo'
    assert doc['rank_agreement']['control'] == 'fifo/fifo'
    assert doc['run']['reference'] == 'k1_off_fifo'
    assert doc['metric']['declared_by'] == 'spec:inbound_unload'
    assert doc['metric']['adjustment']['applies'] is True


def test_the_metric_is_declared_by_the_spec_or_falls_back_out_loud():
    said = []
    m = ur._metric_for({'spec': 'inbound_unload'}, log=said.append)
    assert m['declared_by'] == 'spec:inbound_unload' and m['field'] == 'ss_pick_owed'
    assert m['column'] == 'pick_owed_s' and m['census_column'] == 'unservable_weight'
    m2 = ur._metric_for({'spec': 'a_spec_nobody_registered'}, log=said.append)
    assert m2['declared_by'] == 'default' and m2['field'] == ur.METRIC_FIELD
    assert any('declares no ranking' in s for s in said)
    assert ur._metric_for({}, log=said.append)['declared_by'] == 'default'


def test_batch_rows_adjust_the_score_and_measure_the_floor(tmp_path, monkeypatch):
    """With readable batch rows and a planned weight, the ranking score is the ADJUSTED
    steady-state mean, the floor is measured, and both are stated on the record."""
    from types import SimpleNamespace as _S
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})
    import numpy as np
    n = 12
    jitter = np.random.default_rng(5).normal(0.0, 0.5, n)   # PAIRED: the same draw in both cells
    jitter -= jitter.mean()                                 # zero-mean, so the sums are exact

    # fifo: 100 s a batch, no census. lifo: 99 s a batch but 10 of 1,000 lines unpriced,
    # so adjusted it is 100 s -- an exact tie with fifo once the census is priced.  The
    # jitter is not periodic on purpose: an alternating +-0.5 collapses under a block
    # bootstrap of block length 2 to intervals of zero width.
    def rows(rt, run, arm):
        lifo = 'k1_off_lifo' in os.path.normpath(run.path).split(os.sep)
        owed, cen = (99.0, 10.0) if lifo else (100.0, 0.0)
        return ([_S(batch_id=b, pick_owed_s=owed + jitter[b], unservable_weight=cen)
                 for b in range(n)], n)
    monkeypatch.setattr(ur, '_leaf_batch_rows', rows)
    monkeypatch.setattr(ur._PlannedWeights, 'total', lambda self, *a, **k: 1000.0)
    doc = ur.rank(root, log=lambda *_a: None)
    win = doc['primary_pair']
    w = {e['cell']: e for e in doc['rankings'][win]}
    # two units a cell, two leaves a unit: 4 x 100 s
    assert w['k1_off_fifo']['owed_seconds'] == pytest.approx(400.0)
    assert w['k1_off_lifo']['owed_seconds'] == pytest.approx(400.0)
    assert w['k1_off_lifo']['owed_unadjusted'] == pytest.approx(2 * (3650.0 + 3600.0))
    assert w['k1_off_lifo']['adjusted'] is True
    nf = doc['noise_floor'][win]
    assert nf['source'] == 'measured' and nf['floor_pct'] is not None
    assert nf['cells']['k1_off_lifo']['n_batches'] == n
    # not exactly zero: the +-0.5 jitter is scaled by the adjustment too, so the paired gap
    # is a few hundred-thousandths of a percent -- which is the point of measuring it
    assert nf['cells']['k1_off_lifo']['gap_pct'] == pytest.approx(0.0, abs=1e-3)
    assert w['k1_off_lifo']['gap_ci_pct'] is not None
    assert all(u['adjusted'] and u['planned_weight'] == {'store': 1000.0, 'fulfillment': 1000.0}
               for u in doc['units'])
    assert all('by_batch' not in u for u in doc['units'])
    # --declared-floor forces the declared value even with rows present
    doc2 = ur.rank(root, noise_floor_pct=0.3, declared_floor=True, log=lambda *_a: None)
    assert doc2['noise_floor'][win]['applied_pct'] == 0.3
    assert doc2['noise_floor'][win]['source'].startswith('declared')


# ── the test reviewer's four gaps (2026-09-22), each a silent-failure path ──────────

def test_the_measured_floor_is_the_one_applied_and_it_groups_the_cells(tmp_path, monkeypatch):
    """A regression that measured the floor, wrote it to the record and then ranked on the
    declared value would pass the adjust-and-measure test: its two stubs tie under either.
    So pin that `applied_pct` IS the measured value, and that it moved a decision: two cells
    0.02% apart on the mean, with batch-to-batch noise ten times that on one of them, tie
    under the measured floor and separate under a declared 0.001% one."""
    import numpy as np
    from types import SimpleNamespace as _S
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})
    n = 40
    noise = np.random.default_rng(3).normal(0.0, 0.3, n)      # lifo's own, unpaired noise
    noise -= noise.mean()                                    # zero-mean: the gap IS the offset

    def rows(rt, run, arm):
        lifo = 'k1_off_lifo' in os.path.normpath(run.path).split(os.sep)
        base = 100.05 if lifo else 100.0                      # a 0.05% gap, exactly
        return ([_S(batch_id=b, pick_owed_s=base + (noise[b] if lifo else 0.0),
                    unservable_weight=0.0) for b in range(n)], n)
    monkeypatch.setattr(ur, '_leaf_batch_rows', rows)
    monkeypatch.setattr(ur._PlannedWeights, 'total', lambda self, *a, **k: 1000.0)
    doc = ur.rank(root, noise_floor_pct=0.001, log=lambda *_a: None)
    win = doc['primary_pair']
    nf = doc['noise_floor'][win]
    assert nf['source'] == 'measured', nf
    assert nf['applied_pct'] == pytest.approx(nf['floor_pct']), nf
    gap = nf['cells']['k1_off_lifo']['gap_pct']
    assert gap == pytest.approx(0.05, abs=1e-6), nf
    assert gap < nf['applied_pct'], nf                # the gap sits inside its own interval
    assert nf['applied_pct'] > 0.001, nf              # and the declared value would not
    assert {e['tie_group'] for e in doc['rankings'][win]} == {1}, doc['rankings'][win]
    assert all(e['decided_by'] == 'overage' for e in doc['rankings'][win])
    # the same rows under the DECLARED 0.001% floor separate them: the floor decided
    doc2 = ur.rank(root, noise_floor_pct=0.001, declared_floor=True, log=lambda *_a: None)
    assert {e['tie_group'] for e in doc2['rankings'][win]} == {1, 2}, doc2['rankings'][win]
    assert doc2['noise_floor'][win]['cells'] == {}
    assert all(u['adjusted'] for u in doc2['units'])   # the floor and the adjustment are independent


def test_a_half_adjustable_unit_ranks_raw_and_poisons_its_cells_floor(tmp_path, monkeypatch):
    """One leaf with batch rows and a weight, the other without: the unit must rank on the
    series scalar and say so, its per-batch form must NOT be built from the one leaf, and
    the pair's floor must fall back to declared because the cell is unmeasured."""
    from types import SimpleNamespace as _S
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})

    def rows(rt, run, arm):
        if run.channel == 'fulfillment':
            return None                               # no DB for this leaf
        return ([_S(batch_id=b, pick_owed_s=99.0, unservable_weight=10.0) for b in range(6)], 6)
    monkeypatch.setattr(ur, '_leaf_batch_rows', rows)
    monkeypatch.setattr(ur._PlannedWeights, 'total', lambda self, *a, **k: 1000.0)
    doc = ur.rank(root, log=lambda *_a: None)
    win = doc['primary_pair']
    for u in doc['units']:
        assert u['adjusted'] is False, u
        assert u['owed_seconds'] == u['owed_unadjusted'], u
        assert u['planned_weight'] is None, u
    nf = doc['noise_floor'][win]
    assert nf['floor_pct'] is None and nf['source'].startswith('declared'), nf
    # and the mirror case: rows on both leaves but no weight for one of them
    monkeypatch.setattr(ur, '_leaf_batch_rows', lambda rt, run, arm: (
        [_S(batch_id=b, pick_owed_s=99.0, unservable_weight=10.0) for b in range(6)], 6))
    monkeypatch.setattr(ur._PlannedWeights, 'total',
                        lambda self, cell, run, meta, arm, n: (None if run.channel == 'store'
                                                               else 1000.0))
    doc = ur.rank(root, log=lambda *_a: None)
    assert all(u['adjusted'] is False and u['owed_seconds'] == u['owed_unadjusted']
               for u in doc['units']), doc['units']


def test_a_frequency_weighted_census_is_never_priced_by_a_line_total(tmp_path, monkeypatch):
    """A worker that could not load its batch list weights the score by catalogue
    frequency (sums to ~1), so its census is a fraction; pricing that by a line total would
    adjust by nothing and still print `adjusted: true`."""
    from types import SimpleNamespace as _S
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})
    monkeypatch.setattr(ur, '_leaf_batch_rows', lambda rt, run, arm: (
        [_S(batch_id=b, pick_owed_s=99.0, unservable_weight=0.004) for b in range(6)], 6))
    monkeypatch.setattr(ur._PlannedWeights, 'total', lambda self, *a, **k: 1000.0)
    said = []
    doc = ur.rank(root, log=said.append)
    assert all(u['adjusted'] is False for u in doc['units'])
    assert any('frequency-weighted' in s for s in said)
    # the pure predicate, both ways
    assert ur._basis_is_script([_S(unservable_weight=0.0), _S(unservable_weight=12.0)],
                               'unservable_weight') is True
    assert ur._basis_is_script([_S(unservable_weight=0.5)], 'unservable_weight') is False
    assert ur._basis_is_script([_S(unservable_weight=0.5)], None) is True


def test_planned_weights_resolve_by_fingerprint_then_by_sku_overlap_over_the_planned_prefix(
        tmp_path, monkeypatch):
    """The three ways `_PlannedWeights.total` finds a leaf's batch list, and the one way it
    refuses: the recorded fingerprint wins; without one the pickle whose SKUs are the leaf's
    wins; a leaf whose SKUs match no pickle gets None and one log line; and the total is
    over the PLANNED prefix, never the whole pickle."""
    from types import SimpleNamespace as _S
    store = [_S(items={1: 1, 2: 1}), _S(items={1: 1}), _S(items={3: 1})]     # 4 lines, 3 in 2 batches
    ful = [_S(items={101: 1, 102: 1}), _S(items={101: 1})]
    blobs = {'/p/_batches_aaaa.pkl': ('aaaa-full', store), '/p/_batches_bbbb.pkl': ('bbbb-full', ful)}
    monkeypatch.setattr('Optimization.simdriver.batch_precompute.read_batches_blob',
                        lambda path: blobs.get(path))

    class _RT:
        def glob(self, art, **parts):
            assert art == 'batches_cache' and parts == {'cell': 'c', 'pair': 'prof_a'}
            return sorted(blobs)

        def leaf_path(self, run, art, **k):
            return f'{run.path}/sim_x.db'
    said = []
    w = ur._PlannedWeights(_RT(), said.append)
    run_s = _S(pair='prof_a', channel='store', config='store', path='/c/prof_a/store/store')
    run_f = _S(pair='prof_a', channel='fulfillment', config='ful', path='/c/prof_a/ful/fulfillment')
    # (a) the fingerprint wins, and the total is over the planned prefix (2 of 3 batches)
    assert w.total('c', run_s, {'batches_fingerprint': 'aaaa-full'}, 'arm', 2) == 3.0
    assert w.total('c', run_s, {'batches_fingerprint': 'aaaa-full'}, 'arm', None) == 4.0
    # (b) no fingerprint: the pickle covering the leaf's SKUs
    monkeypatch.setattr(ur._PlannedWeights, '_leaf_skus',
                        lambda self, run, arm: {101, 102} if run.channel == 'fulfillment' else {1, 2, 3})
    assert w.total('c', run_f, {}, 'arm', 2) == 3.0
    assert w.total('c', run_s, {}, 'arm', 3) == 4.0
    # (c) a leaf matching no pickle: None, said once
    monkeypatch.setattr(ur._PlannedWeights, '_leaf_skus', lambda self, run, arm: {999})
    run_x = _S(pair='prof_a', channel='store', config='store', path='/c/prof_a/other/store')
    assert w.total('c', run_x, {}, 'arm', 3) is None
    assert w.total('c', run_x, {}, 'arm', 3) is None
    assert sum('no batch list matches' in s for s in said) == 1
    # (d) a fingerprint nothing carries resolves to nothing, not to an overlap guess
    assert w.total('c', run_s, {'batches_fingerprint': 'zzzz'}, 'arm', 3) is None


def test_a_spec_declared_metric_replaces_the_default_fields(monkeypatch):
    """`PHASE2_RANKING` is value-identical to the default, so the merge could be deleted
    and only `declared_by` would notice.  A spec that declares a DIFFERENT metric must
    change what ranks, and a census-less one must leave the score unadjusted while still
    measuring the floor."""
    from types import SimpleNamespace as _S
    from Optimization.config import whatif_config as wc
    monkeypatch.setitem(wc.SPECS, 'a_fill_trial', {'ranking': {
        'quantity': 'production_seconds', 'field': 'ss_prod_hours', 'column': 'duration',
        'census_quantity': None, 'census_field': None, 'census_column': None,
        'exact_field': None}})
    m = ur._metric_for({'spec': 'a_fill_trial'}, log=lambda *_a: None)
    assert m['field'] == 'ss_prod_hours' and m['column'] == 'duration'
    assert m['census_column'] is None and m['declared_by'] == 'spec:a_fill_trial'
    # a census-less metric: the raw column per batch, no adjustment, a per-batch form
    rows = [_S(batch_id=b, duration=10.0 + b) for b in range(4)]
    ss, by_batch = ur._adjusted_leaf(rows, m, None)
    assert by_batch == {0: 10.0, 1: 11.0, 2: 12.0, 3: 13.0} and ss == pytest.approx(11.5)


# ── the re-pick share (2026-09-22, Experiment 9's readers) ───────────────────────────

def test_inbound_repick_counts_units_picked_from_bins_a_reorder_placed_earlier():
    """Two shares at unit grain.  Bin A: 10 units placed at batch 2, 6 picked at batch 3 and
    8 at batch 5 -- served 14, repicked min(14, 10) = 10.  Bin B: 4 placed at batch 4, picked
    2 at batch 1 (BEFORE the placement: not served, not repicked).  Bin C: never placed by a
    reorder, 5 picked.  An `initial` placement row does not count as inbound."""
    picks = [
        {'sku': 1, 'batch_id': 3, 'aisle_id': 1, 'bayX': 0, 'bayY': 0, 'units': 6},
        {'sku': 1, 'batch_id': 5, 'aisle_id': 1, 'bayX': 0, 'bayY': 0, 'units': 8},
        {'sku': 2, 'batch_id': 1, 'aisle_id': 1, 'bayX': 1, 'bayY': 0, 'units': 2},
        {'sku': 3, 'batch_id': 2, 'aisle_id': 2, 'bayX': 0, 'bayY': 0, 'units': 5},
    ]
    placements = [
        {'sku': 1, 'batch_id': 2, 'aisle_id': 1, 'bayX': 0, 'bayY': 0, 'qty': 10, 'cause': 'reorder'},
        {'sku': 2, 'batch_id': 4, 'aisle_id': 1, 'bayX': 1, 'bayY': 0, 'qty': 4, 'cause': 'reorder'},
        {'sku': 3, 'batch_id': 0, 'aisle_id': 2, 'bayX': 0, 'bayY': 0, 'qty': 9, 'cause': 'initial'},
    ]
    r = ur.inbound_repick(picks, placements)
    assert r['picked_units'] == 21 and r['served_units'] == 14
    assert r['served_share'] == pytest.approx(14 / 21)
    assert r['placed_units'] == 14 and r['repicked_units'] == 10
    assert r['repicked_share'] == pytest.approx(10 / 14)
    # nothing placed, nothing picked: shares are None, never a division
    e = ur.inbound_repick([], [])
    assert e['served_share'] is None and e['repicked_share'] is None
    # the same bin placed twice: the EARLIEST placement opens the window, units add up
    twice = placements[:1] + [dict(placements[0], batch_id=4, qty=3)]
    r2 = ur.inbound_repick(picks[:2], twice)
    assert r2['placed_units'] == 13 and r2['repicked_units'] == 13 and r2['served_units'] == 14


def test_the_ranking_records_each_cells_threshold_and_the_repick_shares(tmp_path, monkeypatch):
    root = _root(tmp_path, {'k1_off_fifo': _arms(3600.0, 3600.0, 7200.0),
                            'k1_off_lifo': _arms(3650.0, 3600.0, 7000.0)})
    _patch_site(monkeypatch, {})
    monkeypatch.setattr(ur, '_leaf_repick', lambda rt, run, arm: {
        'picked_units': 100.0, 'served_units': 20.0, 'served_share': 0.2,
        'placed_units': 50.0, 'repicked_units': 10.0, 'repicked_share': 0.2})
    doc = ur.rank(root, log=lambda *_a: None)
    assert doc['metric']['tie_break']['threshold_days'] == {'k1_off_fifo': 0.4, 'k1_off_lifo': 0.4}
    win = doc['primary_pair']
    rec = doc['inbound_repick'][win]['k1_off_fifo']['store']
    assert rec['n_units'] == 2 and rec['picked_units'] == 200.0
    assert rec['served_share'] == pytest.approx(0.2) and rec['repicked_share'] == pytest.approx(0.2)
    assert all(u['inbound_repick']['store']['served_share'] == 0.2 for u in doc['units'])
