"""test_cell_is_complete.py — what a resume is allowed to skip, and the torn pair it used to.

A resume skips a cell the predicate calls complete.  A skipped cell is never set up, never
reaches the work pool, and never reaches the coupled reconciler — so if the predicate is
wrong in the generous direction, the run EXITS 0 with a torn pair and nothing anywhere says
so.  That was the state until 2026-09-19: `simdriver.cells._cell_complete` accepted one
`sim_meta.json` per PAIR, so a cell with two of eight leaves finalized read complete.

`RunTree.cell_is_complete` replaces it.  The four decisions with a silent failure mode, each
pinned below:

  * **The leaf count is a UNION, not a product.**  Store configs make `<store_cfg>/store`,
    fulfillment configs make `<ff_cfg>/fulfillment`.  A cross-product rule would make every
    mixed cell permanently incomplete, which kills the skip and turns every resume into a
    full re-run — a failure that costs hours and looks like caution.
  * **Mixedness is PER PAIR and read from disk**, because the descriptor's channel list is
    derived from CONFIG before any catalogue is read.  A run really can carry a mixed
    catalogue for one inventory pair and a store-only one for another.
  * **A partly-written setup is NOT a smaller target.**  The wanted count comes from the
    `config_json` records setup wrote; if that count is neither the store-only shape nor the
    mixed one, setup itself was interrupted and the cell is incomplete.
  * **An in-flight file beats a finalized marker.**  A leaf killed inside its finalize
    window can have a `sim_meta.json` and still be mid-write; `resume.pkl` and the
    checkpoints are removed at finalize, so their presence means an arm was live.

And one that is not a failure mode but a vacuity guard: a genuinely finished cell must be
COMPLETE.  Without that, every assertion here is satisfied by a predicate that returns False.

The tree is planted by hand rather than simulated: these are file-count questions, and a
real two-cell run to ask them would be a twenty-minute test.

Run:  python -m pytest Tests/unit/test_cell_is_complete.py -q
"""
from __future__ import annotations

import json
import os

from Optimization import runschema


# ── planting a run tree ──────────────────────────────────────────────────────────────

def _leaf(root, cell, pair, config, channel, *, finalized=True, arms=('uni_fifo_norsl',)):
    """One analysis leaf: its config record, its sim DBs, and optionally its sim_meta."""
    cfg_dir = os.path.join(root, cell, pair, config)
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump({'config': config}, f)
    leaf = os.path.join(cfg_dir, channel) if channel else cfg_dir
    os.makedirs(leaf, exist_ok=True)
    for arm in arms:
        with open(os.path.join(leaf, f'sim_{arm}.db'), 'wb') as f:
            f.write(b'SQLite format 3\x00' + bytes(64))
    if finalized:
        with open(os.path.join(leaf, 'sim_meta.json'), 'w', encoding='utf-8') as f:
            json.dump({'name': config, 'run_dir': leaf, 'inventory': pair,
                       'channel': channel, 'strategies': [{'key': a} for a in arms]}, f)
    return leaf


def _layout(root, cells, pairs, *, store=('store',), ful=('ful',)):
    from Optimization.runschema import contract
    doc = {'version': 3, 'schema_id': contract.head(), 'kind': 'sweep', 'spec': 'planted',
           'base': os.path.basename(root), 'reference': cells[0],
           'cells': [{'name': c} for c in cells], 'pairs': list(pairs),
           'channels': ['store', 'fulfillment'],
           'configs': {'store': list(store), 'fulfillment': list(ful)}, 'arms': None}
    with open(os.path.join(root, 'run_layout.json'), 'w', encoding='utf-8') as f:
        json.dump(doc, f)


def _root(tmp_path, name='comparison_whatif_20260919_000000'):
    root = str(tmp_path / name)
    os.makedirs(root, exist_ok=True)
    return root


def _rt(root):
    return runschema.resolver_for(root)


# ── the happy case, first, because everything else is vacuous without it ─────────────

def test_a_finished_mixed_cell_is_complete(tmp_path):
    """NON-VACUITY FOR THE WHOLE FILE.  Every other assertion here is satisfied by a
    predicate that always answers False; this is the one that is not."""
    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a'])
    _leaf(root, 'c1', 'pair_a', 'store', 'store')
    _leaf(root, 'c1', 'pair_a', 'ful', 'fulfillment')
    done, why = _rt(root).cell_is_complete('c1', ['pair_a'])
    assert done, why
    assert '2/2' in why, why


def test_a_store_only_pair_is_complete_with_one_leaf(tmp_path):
    """Mixedness is per pair.  This pair's catalogue produced no fulfillment section, so its
    one config record IS its whole shape — and the layout still declares a fulfillment
    config, which is exactly the case a run-wide `mixed` flag gets wrong."""
    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a'])
    _leaf(root, 'c1', 'pair_a', 'store', 'store')
    done, why = _rt(root).cell_is_complete('c1', ['pair_a'])
    assert done, why


def test_one_mixed_pair_beside_one_store_only_pair_in_the_same_cell(tmp_path):
    """The shape a run-wide flag cannot express, in one cell."""
    root = _root(tmp_path)
    _layout(root, ['c1'], ['mixed_pair', 'store_pair'])
    _leaf(root, 'c1', 'mixed_pair', 'store', 'store')
    _leaf(root, 'c1', 'mixed_pair', 'ful', 'fulfillment')
    _leaf(root, 'c1', 'store_pair', 'store', 'store')
    done, why = _rt(root).cell_is_complete('c1', ['mixed_pair', 'store_pair'])
    assert done, why
    assert '3/3' in why, why


# ── the torn cases ───────────────────────────────────────────────────────────────────

def test_one_finalized_leaf_of_a_mixed_pair_is_NOT_complete(tmp_path):
    """THE DEFECT, exactly.  The old predicate globbed for any `sim_meta.json` under the
    pair and answered True here, so a resume skipped the cell and the fulfillment half of
    the pair was never run — and the run still exited 0."""
    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a'])
    _leaf(root, 'c1', 'pair_a', 'store', 'store')
    _leaf(root, 'c1', 'pair_a', 'ful', 'fulfillment', finalized=False)
    done, why = _rt(root).cell_is_complete('c1', ['pair_a'])
    assert not done
    assert '1/2' in why, why


def test_a_leaf_killed_inside_its_finalize_window_is_NOT_complete(tmp_path):
    """Every marker present and an arm still live.  `sim_meta.json` is written before the
    transients are removed, so a kill in that window leaves a tree that counts as finished
    and is not."""
    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a'])
    leaf = _leaf(root, 'c1', 'pair_a', 'store', 'store')
    _leaf(root, 'c1', 'pair_a', 'ful', 'fulfillment')
    rt = _rt(root)
    resume = rt.path('resume_pkl', cell='c1', pair='pair_a', config='store', channel='store',
                     strategy='uni_fifo_norsl')
    os.makedirs(os.path.dirname(resume), exist_ok=True)
    with open(resume, 'wb') as f:
        f.write(b'\x80\x04')
    assert os.path.dirname(resume) == leaf, (
        f'the contract puts resume_pkl at {resume}, not in the leaf this test planted; the '
        f'fixture and the contract have diverged')

    done, why = rt.cell_is_complete('c1', ['pair_a'])
    assert not done
    assert 'in-flight' in why, why

    # ...and it IS complete once the transient is gone, so it is the file doing the work.
    os.remove(resume)
    assert rt.cell_is_complete('c1', ['pair_a'])[0]


def test_a_half_written_setup_is_not_a_smaller_target(tmp_path):
    """A pair with a config count that is neither shape: setup itself was interrupted.

    The dangerous reading is "this pair wants what is on disk", which any partly-written
    setup satisfies trivially — the fewer records the interruption left, the easier the cell
    is to call finished.
    """
    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a'], store=('store_a', 'store_b'), ful=('ful',))
    _leaf(root, 'c1', 'pair_a', 'store_a', 'store')          # 1 of 2 store configs, no ful
    done, why = _rt(root).cell_is_complete('c1', ['pair_a'])
    assert not done
    assert 'neither the store-only shape' in why, why


def test_a_cell_that_never_started_is_not_complete(tmp_path):
    root = _root(tmp_path)
    _layout(root, ['c1', 'c2'], ['pair_a'])
    _leaf(root, 'c1', 'pair_a', 'store', 'store')
    _leaf(root, 'c1', 'pair_a', 'ful', 'fulfillment')
    rt = _rt(root)
    assert rt.cell_is_complete('c1', ['pair_a'])[0]
    done, why = rt.cell_is_complete('c2', ['pair_a'])
    assert not done and 'does not exist' in why, why


def test_a_missing_pair_is_not_complete(tmp_path):
    """The second pair was never set up at all: zero config records, which is not a shape."""
    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a', 'pair_b'])
    _leaf(root, 'c1', 'pair_a', 'store', 'store')
    _leaf(root, 'c1', 'pair_a', 'ful', 'fulfillment')
    done, why = _rt(root).cell_is_complete('c1', ['pair_a', 'pair_b'])
    assert not done
    assert 'pair_b' in why, why


def test_it_refuses_rather_than_guessing_without_a_layout(tmp_path):
    """No declared configs means no derivable leaf count, and the safe answer is NOT
    complete: a resume that re-runs a finished cell wastes hours, one that skips an
    unfinished cell loses it."""
    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a'], store=(), ful=())
    _leaf(root, 'c1', 'pair_a', 'store', 'store')
    done, why = _rt(root).cell_is_complete('c1', ['pair_a'])
    assert not done
    assert 'no configs' in why, why


def test_pairs_default_to_the_layouts_when_none_are_passed(tmp_path):
    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a'])
    _leaf(root, 'c1', 'pair_a', 'store', 'store')
    _leaf(root, 'c1', 'pair_a', 'ful', 'fulfillment')
    rt = _rt(root)
    assert rt.cell_is_complete('c1') == rt.cell_is_complete('c1', ['pair_a'])


def test_archive_cells_delegates_to_the_one_predicate(tmp_path):
    """Two predicates for one question is how this went wrong: the strict one guarded
    archiving and the loose one guarded resuming, and only the loose one could lose data."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'scripts'))
    import archive_cells as ac

    root = _root(tmp_path)
    _layout(root, ['c1'], ['pair_a'])
    _leaf(root, 'c1', 'pair_a', 'store', 'store')
    _leaf(root, 'c1', 'pair_a', 'ful', 'fulfillment', finalized=False)
    rt = _rt(root)
    layout = rt.layout
    # The `mixed` argument is ignored now; both values must give the resolver's answer.
    want = rt.cell_is_complete('c1', ['pair_a'])
    assert ac.cell_state(rt, 'c1', layout, True) == want
    assert ac.cell_state(rt, 'c1', layout, False) == want
    assert not want[0]


def test_archivable_no_longer_reasons_from_cell_ordering(tmp_path):
    """The flat work pool made every cell overlap, so "a later cell started" stopped being
    evidence that an earlier one is finished.  The in-flight probe is the direct evidence
    that replaced it, and it is what keeps a live cell out."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'scripts'))
    import archive_cells as ac

    root = _root(tmp_path)
    _layout(root, ['c1', 'c2'], ['pair_a'])
    for cell in ('c1', 'c2'):
        _leaf(root, cell, 'pair_a', 'store', 'store')
        _leaf(root, cell, 'pair_a', 'ful', 'fulfillment')
    rt = _rt(root)

    # c2 is LAST, so it is held back without the caller's assertion; c1 is offered even
    # though nothing "later started" in the old sense.
    assert [n for n, _w in ac.archivable(rt, rt.layout, True, False)] == ['c1']
    assert [n for n, _w in ac.archivable(rt, rt.layout, True, True)] == ['c1', 'c2']

    # A live arm in c1 takes it back off the list, which is the property the ordering test
    # was standing in for.
    resume = rt.path('resume_pkl', cell='c1', pair='pair_a', config='store',
                     channel='store', strategy='uni_fifo_norsl')
    with open(resume, 'wb') as f:
        f.write(b'\x80\x04')
    assert [n for n, _w in ac.archivable(rt, rt.layout, True, True)] == ['c2']


# ── the driver's own skip decision, through the production selector ──────────────────

def test_a_resume_skips_the_clean_cell_and_re_runs_the_torn_one(tmp_path):
    """THE WHOLE POINT, through `scenario._cells_to_run` rather than a copy of its rule.

    Two planted cells, a real descriptor, `resume=True`: the torn cell reaches the driver
    and the clean one does not. Before this change both were skipped, the run exited 0, and
    the fulfillment half of the torn pair was simply missing from the analysis.
    """
    import logging
    from Optimization.simdriver import scenario as sc
    from Optimization.simdriver.cells import Cell

    root = _root(tmp_path)
    _layout(root, ['clean', 'torn'], ['pair_a'])
    _leaf(root, 'clean', 'pair_a', 'store', 'store')
    _leaf(root, 'clean', 'pair_a', 'ful', 'fulfillment')
    _leaf(root, 'torn', 'pair_a', 'store', 'store')
    _leaf(root, 'torn', 'pair_a', 'ful', 'fulfillment', finalized=False)

    cells = [Cell('clean', None, {'enabled': False}, 'round_robin'),
             Cell('torn', None, {'enabled': False}, 'round_robin')]
    pairs = [('pair_a', 'inv.db', 'aff.db')]
    log = logging.getLogger('test_cell_is_complete')

    rt, todo = sc._cells_to_run(root, cells, pairs, True, log)
    assert rt is not None
    assert [c.name for c in todo] == ['torn'], [c.name for c in todo]

    # NON-VACUITY: without `resume` nothing is skipped, so it is the predicate deciding and
    # not the selector always returning one cell.
    _rt2, all_todo = sc._cells_to_run(root, cells, pairs, False, log)
    assert [c.name for c in all_todo] == ['clean', 'torn']
    assert _rt2 is None, 'a fresh run must not even build the resolver to decide'


def test_a_root_with_no_descriptor_skips_nothing(tmp_path):
    """The guard that makes the skip safe: no contract, no skipping. Re-running a finished
    cell costs hours; skipping an unfinished one loses it."""
    import logging
    from Optimization.simdriver import scenario as sc
    from Optimization.simdriver.cells import Cell

    root = _root(tmp_path)                       # no run_layout.json at all
    _leaf(root, 'clean', 'pair_a', 'store', 'store')
    cells = [Cell('clean', None, {'enabled': False}, 'round_robin')]
    rt, todo = sc._cells_to_run(root, cells, [('pair_a', 'i', 'a')], True,
                                logging.getLogger('test_cell_is_complete'))
    assert [c.name for c in todo] == ['clean'], (
        'a root the contract cannot describe must have every cell set up again')
    if rt is not None:
        assert not rt.cell_is_complete('clean', ['pair_a'])[0]
