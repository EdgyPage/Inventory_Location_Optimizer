"""test_cell_record.py — a what-if cell is a record, and its baseline predicate is one thing.

The cell was a bare 4-tuple `(name, split, zoning, scheduler)`, unpacked positionally in
five files, with the reference-cell predicate written out verbatim in two of them. Those
two MUST agree: one picks the cell the driver treats as the baseline, the other writes its
name into `run_layout.json` for every downstream what-if to diff against. Two copies of a
predicate that must agree is a bug with a delay on it.

The record is a `NamedTuple` deliberately. `c[1]`, `c[3]` and
`(name, split, zoning, sched) = c` all keep working, so every existing consumer was correct
before it was touched and the change cannot break one by being half-applied. That
compatibility is the safety argument, so it is what these tests check first.

The point of the change is the FIFTH axis. An inbound sorter policy is now one field and
one line in `_build_cells`, not five positional unpacks to find and renumber.

Run:  python -m pytest Tests/unit/test_cell_record.py -q
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from Optimization.simdriver.cells import _SCHED_SHORT, Cell, _build_cells, reference_cell

_SPEC = {
    'zoning': [('off', {'enabled': False}), ('abc', {'enabled': True, 'mode': 'abc'})],
    'ks': [1, 2],
    'losses': [0.1],
    'schedulers': ['round_robin', 'lpt'],
}


# ── backward compatibility is the safety argument ────────────────────────────────

def test_a_cell_is_still_indexable_positionally():
    """Five files index it. If this breaks, the change was never safe."""
    c = _build_cells(_SPEC)[0]
    assert c[0] == c.name
    assert c[1] == c.split
    assert c[2] == c.zoning
    assert c[3] == c.scheduler


def test_a_cell_still_unpacks_into_four_names():
    name, split, zoning, sched = _build_cells(_SPEC)[0]
    assert isinstance(name, str) and isinstance(zoning, dict)
    assert split is None or isinstance(split, dict)
    assert sched in _SCHED_SHORT


def test_a_cell_is_still_a_tuple():
    c = _build_cells(_SPEC)[0]
    assert isinstance(c, tuple) and len(c) == 4


# ── one predicate, not two ───────────────────────────────────────────────────────

def test_the_reference_is_no_split_no_zoning_legacy_scheduler():
    cells = _build_cells(_SPEC)
    ref = [c for c in cells if c.is_reference]
    assert len(ref) == 1, f'{len(ref)} cells claim to be the reference'
    assert ref[0].split is None
    assert not ref[0].zoning.get('enabled')
    assert ref[0].scheduler == 'round_robin'
    assert reference_cell(cells) == ref[0].name


def test_the_fallback_is_used_only_when_no_cell_qualifies():
    zoned_only = [Cell('a', None, {'enabled': True}, 'lpt'),
                  Cell('b', {'k': 2}, {'enabled': False}, 'round_robin')]
    assert reference_cell(zoned_only, 'declared') == 'declared'
    assert reference_cell(zoned_only) == 'a', 'with no fallback, the first cell'


@pytest.mark.parametrize('module,fn', [
    ('Optimization/simdriver/scenario.py', '_run_whatif_matrix'),
    ('Optimization/run_simulation.py', 'main'),
])
def test_neither_call_site_still_spells_the_predicate_itself(module, fn):
    """The verbatim duplicate is what this commit removes; a re-inlined copy is the
    regression. Scanned as code with docstrings stripped — prose describing the retired
    predicate is not the predicate."""
    import pathlib
    root = pathlib.Path(inspect.getfile(Cell)).parents[2]
    tree = ast.parse((root / module).read_text(encoding='utf-8'))
    target = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == fn), None)
    assert target is not None, f'{fn} not found in {module}'
    body = target.body[1:] if (isinstance(target.body[0], ast.Expr)
                               and isinstance(target.body[0].value, ast.Constant)) else target.body
    code = '\n'.join(ast.unparse(n) for n in body)
    assert "'round_robin'" not in code or 'reference_cell' in code, (
        f'{module}:{fn} tests the scheduler itself instead of asking reference_cell')
    assert 'reference_cell(' in code, f'{module}:{fn} no longer shares the predicate'


# ── the descriptor derives its keys from the record ──────────────────────────────

def test_run_layout_cell_keys_are_the_record_field_names():
    """`sim_manifest` used to restate the four keys. `_asdict()` means the descriptor and
    the producer cannot drift, which is what a fifth axis needs."""
    c = _build_cells(_SPEC)[0]
    assert list(c._asdict()) == list(Cell._fields)
    assert Cell._fields == ('name', 'split', 'zoning', 'scheduler')


def test_the_descriptor_still_accepts_a_legacy_plain_tuple():
    """A resumed legacy run reaches the writer with whatever it recorded."""
    import pathlib
    root = pathlib.Path(inspect.getfile(Cell)).parents[2]
    src = (root / 'Optimization' / 'runschema' / 'sim_manifest.py').read_text(encoding='utf-8')
    assert "_asdict() if hasattr(c, '_asdict')" in src
    assert "dict(zip(('name', 'split', 'zoning', 'scheduler'), c))" in src


# ── the matrix itself still builds what it built ─────────────────────────────────

def test_the_cell_names_and_order_are_unchanged():
    """Cell names are directory names in every archived run; the order is the order the
    driver walks them."""
    assert [c.name for c in _build_cells(_SPEC)] == [
        'k1_off_rr', 'k2_l10_off_rr', 'k1_abc_rr', 'k2_l10_abc_rr',
        'k1_off_lpt', 'k2_l10_off_lpt', 'k1_abc_lpt', 'k2_l10_abc_lpt',
    ]


def test_a_single_scheduler_sweep_adds_no_suffix():
    """Byte-identical naming for every run that does not sweep the scheduler — which is
    every run before Experiment 8."""
    one = dict(_SPEC, schedulers=['round_robin'])
    assert [c.name for c in _build_cells(one)] == [
        'k1_off', 'k2_l10_off', 'k1_abc', 'k2_l10_abc']


# ── the remaining duplication, named rather than fixed ───────────────────────────

def test_the_scheduler_suffix_and_the_name_parser_share_one_vocabulary():
    """`run_whatif_labor._scheduler_of` still recovers the swept axis by PARSING the cell
    directory name, even though `run_layout.json` records it as a named field. Removing
    that is a change to an analysis module's output and needs its own evidence, so it is
    not in this commit — but the two encodings are tied here so they cannot drift apart
    while it waits.
    """
    import pathlib
    root = pathlib.Path(inspect.getfile(Cell)).parents[2]
    src = (root / 'Optimization' / 'run_whatif_labor.py').read_text(encoding='utf-8')
    parsed = next(l for l in src.splitlines() if l.strip().startswith('_SCHED'))
    for short in _SCHED_SHORT.values():
        assert f"'{short}'" in parsed, (
            f'the cell name can end in _{short} but the what-if parser does not know it')
