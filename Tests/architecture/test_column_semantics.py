"""test_column_semantics.py — the completeness gate over declared column semantics.

Shape is declared and enforced; semantics were prose until `Schema/semantics.py`.  This
ratchet makes the declarations load-bearing: every column of every COVERED table tagged,
every tag naming a real column, null-meaning wherever the DDL allows NULL, a clock wherever
the unit is time — and the guards refusing exactly the reads that produced the incident
ledger (a LEVEL summed 101x high, packs added to pieces, row-free aggregation over a
value-dependent kind).

DELIBERATELY STDLIB-ONLY: six of the thirteen architecture tests importorskip pyyaml and
vanish without it — the silent-trap CLAUDE.md documents.  This gate imports nothing
optional, so it cannot vanish.

Run:  python -m pytest Tests/architecture/test_column_semantics.py -q
"""
from __future__ import annotations

import pytest

from Optimization.persistence.Picking_Data import KEYFRAME_DB_FAMILY, SIM_DB_FAMILY
from Optimization.persistence.runtime_metrics import RUNTIME_DB_FAMILY
from Optimization.persistence.Warehouse_Data import WAREHOUSE_DB_FAMILY
from Warehouse.generation.generate_affinity import AFFINITY_DB_FAMILY
from Warehouse.generation.generate_inventory import INVENTORY_DB_FAMILY
from Optimization.persistence.sim_semantics import COVERED, SIM_DB_SEMANTICS
import Optimization.persistence.runtime_semantics    # noqa: F401  (registers on import)
import Optimization.persistence.warehouse_semantics  # noqa: F401
import Warehouse.generation.affinity_semantics       # noqa: F401
import Warehouse.generation.inventory_semantics      # noqa: F401
from Schema import semantics as S

#: Every registered family and its Family object — the no-remainder sweep's scope.
FAMILIES = [
    ('sim_db',             SIM_DB_FAMILY),
    ('keyframes_db',       KEYFRAME_DB_FAMILY),
    ('runtime_metrics_db', RUNTIME_DB_FAMILY),
    ('warehouse_db',       WAREHOUSE_DB_FAMILY),
    ('affinity_db',        AFFINITY_DB_FAMILY),
    ('inventory_db',       INVENTORY_DB_FAMILY),
]


def _shape_tables() -> dict:
    return SIM_DB_FAMILY.declared_shape()['tables']


# ── 1. completeness: the covered tables leave no remainder ───────────────────────

@pytest.mark.parametrize('family,obj', FAMILIES, ids=[f for f, _ in FAMILIES])
def test_every_family_is_completely_and_validly_tagged(family, obj):
    problems = S.check_completeness(obj.declared_shape()['tables'], family)
    assert problems == [], 'declared semantics drifted from the declared shape:\n  ' + \
        '\n  '.join(problems)


def test_every_covered_table_means_every_table():
    """The no-remainder rule itself: a family may not declare a table and leave it out of
    its covered set — that would be a silent carve-out the gate never sees."""
    for family, obj in FAMILIES:
        _sem, covered = S.semantics_for(family)
        shape = set(obj.declared_shape()['tables'])
        assert set(covered) == shape, (
            f'{family}: covered={sorted(covered)} != shape tables={sorted(shape)}')


def test_the_gate_actually_bites():
    """Sabotage-checked, the `test_written_columns_are_readable` discipline: a gate that
    cannot fail proves nothing.  Remove one tag -> caught; invent one -> caught."""
    sem = {t: dict(cols) for t, cols in SIM_DB_SEMANTICS.items()}
    del sem['batch_stats']['recv_cut']
    sem['put_queue_state']['imaginary'] = S.Col(S.FLOW, 'units', 'batch', account=S.PACKS)
    S.register('sim_db_sabotaged', sem, COVERED)
    problems = S.check_completeness(_shape_tables(), 'sim_db_sabotaged')
    assert any('batch_stats.recv_cut: untagged' in p for p in problems)
    assert any('put_queue_state.imaginary' in p and 'no such column' in p for p in problems)


def test_a_time_unit_cannot_be_declared_without_a_clock():
    with pytest.raises(ValueError):
        S.Col(S.SPAN, 's', 'batch')          # seconds on WHICH instrument?
    with pytest.raises(ValueError):
        S.Col(S.SPAN, 'batches', 'batch')    # the legacy countdown is a clock too


# ── 2. the guards refuse the incident ledger ─────────────────────────────────────

ROWS = [{'recv_cut': 3050, 'recv_unloaded': 210, 'queue_depth': 410,
         'in_transit_qty': 5210, 'recv_depth': 3050},
        {'recv_cut': 3055, 'recv_unloaded': 195, 'queue_depth': 388,
         'in_transit_qty': 5002, 'recv_depth': 3055}]


def test_summing_a_level_is_refused_with_its_scar():
    with pytest.raises(S.SemanticsError, match='101x'):
        S.sum_of(ROWS, 'sim_db', 'batch_stats', 'recv_cut')


def test_summing_a_flow_is_allowed():
    assert S.sum_of(ROWS, 'sim_db', 'batch_stats', 'recv_unloaded') == 405


def test_packs_plus_pieces_is_refused_but_packs_plus_packs_is_not():
    with pytest.raises(S.SemanticsError, match='packs are not pieces'):
        S.check_add('sim_db', 'batch_stats', 'queue_depth', 'in_transit_qty')
    S.check_add('sim_db', 'batch_stats', 'queue_depth', 'recv_depth')


def test_cross_clock_ratios_are_refused():
    S.register('two_clocks', {'t': {
        'sim_s':  S.Col(S.SPAN, 's', 'arm', clock=S.SIM),
        'wall_s': S.Col(S.SPAN, 's', 'arm', clock=S.WALL),
    }}, ('t',))
    with pytest.raises(S.SemanticsError, match='different instruments'):
        S.check_ratio('two_clocks', 't', 'sim_s', 't', 'wall_s')
    S.check_ratio('sim_db', 'batch_stats', 'task_makespan', 'batch_stats', 'duration')


def test_a_discriminated_column_refuses_row_free_reads():
    with pytest.raises(S.SemanticsError, match='reason'):
        S.sum_of([], 'sim_db', 'carryover', 'qty')
    with pytest.raises(S.SemanticsError, match='row-free'):
        S.resolve('sim_db', 'carryover', 'qty')
    tag = S.resolve('sim_db', 'carryover', 'qty', row={'reason': 'unpicked_daycut'})
    assert tag.kind == S.FLOW and tag.account == S.PIECES
    tag = S.resolve('sim_db', 'carryover', 'qty', row={'reason': 'dock'})
    assert tag.kind == S.LEVEL and tag.account == S.PACKS


# ── 3. use-assertions: a consumer's declared reads, checked before any query ─────

def test_use_assertions_catch_the_receiving_report_shape():
    clauses = S.validate_uses('sim_db', 'receiving_report', {
        'batch_stats.recv_cut':      'sum',
        'batch_stats.recv_unloaded': 'sum',
        'carryover.qty':             'sum',
        'batch_stats.no_such_col':   'read',
    })
    assert len(clauses) == 3
    assert any('recv_cut' in c and 'LEVEL' in c for c in clauses)
    assert any('carryover.qty' in c and 'per-reason' in c for c in clauses)
    assert any('no_such_col' in c and 'not a declared column' in c for c in clauses)
    assert S.validate_uses('sim_db', 'ok', {'batch_stats.recv_unloaded': 'sum'}) == []


# ── 4. the logical layer: honest names over frozen physical ones ─────────────────

def test_logical_names_resolve_to_their_physical_tags():
    assert S.resolve('sim_db', 'batch_stats', 'release_day') is \
        SIM_DB_SEMANTICS['batch_stats']['work_day']
    assert S.resolve('sim_db', 'work_events', 'frame_index') is \
        SIM_DB_SEMANTICS['work_events']['shift_index']
    with pytest.raises(KeyError):
        S.resolve('sim_db', 'batch_stats', 'not_a_name')


# ── 5. the converted readers declare their uses, and the declarations validate ───

#: The audit's ranked at-risk reads, converted to declared reads.  AST-read (never
#: imported — a viewer module may drag matplotlib) and validated as pure literals.
#: The two bench log-parsers from the audit's list are NOT here: they parse logs, not
#: columns, and the layer's domain is the declared shape.
CONVERTED = [
    'Diagnostics/receiving_report.py',
    'Optimization/Performance_Evaluations/catalog/inventory.py',
    'Optimization/Performance_Evaluations/common/frames.py',
    'Diagnostics/replay_run.py',
    'Optimization/run_whatif_labor.py',
    'Optimization/run_whatif_volume.py',
    'Visualization/readers/base.py',
]


@pytest.mark.parametrize('rel', CONVERTED, ids=[p.split('/')[-1] for p in CONVERTED])
def test_converted_readers_declare_valid_uses(rel):
    import ast as _ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    tree = _ast.parse((root / rel).read_text(encoding='utf-8'))
    uses = None
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Assign) and isinstance(node.targets[0], _ast.Name)
                and node.targets[0].id == 'SEMANTIC_USES'):
            uses = _ast.literal_eval(node.value)
    assert uses, f'{rel}: no SEMANTIC_USES literal — the conversion regressed'
    label = rel.split('/')[-1]
    clauses = [c for family, u in uses.items()
               for c in S.validate_uses(family, label, u)]
    assert clauses == [], '\n'.join(clauses)
