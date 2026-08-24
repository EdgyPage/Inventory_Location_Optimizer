"""test_data_era_gate.py — a quantity may not quietly need data some runs never recorded.

The publish loop has one route it cannot automate: a stakeholder asks for a number the
simulation never wrote down.  That cannot be backfilled — schema identity is derived from
shape and archived files are never rewritten — so the only honest answers are a new sweep
on a vintage that records it, or a recorded caveat and the degraded form.

What made that dangerous was that nothing said which route you were on.  A quantity
reading a column only SOME vetted vintages carry produces a figure on the runs that have
it, no figure at all on the ones that do not, and an INFO line either way.

The gate is `Schema/compat.py`'s own split, applied to the quantity table rather than
reinvented beside it:

    compat.validate(requires) == []   inside the guaranteed surface -> version-free, done
    q.capability names a real key     outside it -> a CAPABILITY, and it is SAID

Neither is a failure.  Being NEITHER is, and that is what these tests refuse.

Run:  python -m pytest Tests/architecture/test_data_era_gate.py -q
"""
from __future__ import annotations

import pytest

import Optimization.persistence.Picking_Data as picking_data
from Optimization.Performance_Evaluations.common import units
from Optimization.Performance_Evaluations.core import era, quantities as Q
from Schema import compat


# ── the gate itself ──────────────────────────────────────────────────────────────

def test_no_quantity_reads_outside_the_guaranteed_surface_unnamed():
    findings = era.findings()
    assert not findings, '\n'.join(findings)


def test_the_module_level_declaration_is_the_union_of_every_quantity_read():
    """`QUANTITY_READS` exists so the schema-compatibility sweep can SEE the declaration.

    A `Requires` reached only through a factory is invisible to that sweep and therefore
    never validated — the exact gap the sweep's own docstring describes. The union has to
    actually be the union, or the object CI validates and the objects the gate checks are
    different things.
    """
    union: dict = {}
    for q in Q.QUANTITIES:
        req = era.requires_for(q)
        if req is None:
            continue
        for table, cols in req.tables.items():
            union.setdefault(table, set()).update(cols)
    assert {t: set(c) for t, c in era.QUANTITY_READS.tables.items()} == union
    assert compat.validate(era.QUANTITY_READS) == []


def test_the_gate_is_not_vacuous():
    """It has to be able to FAIL, or a green run means nothing.

    A quantity reading a column no vetted vintage guarantees must be flagged, and the
    message must offer both honest routes rather than only naming the problem.
    """
    ghost = Q.Quantity(
        key='zz_ghost', label='ghost', axis_stem='ghost', unit=units.NONE,
        direction='lower',
        source=Q.Source(per_batch=('batch', 'a_column_no_vintage_ever_wrote')))
    req = era.requires_for(ghost)
    gaps = compat.validate(req)
    assert gaps, 'compat.validate accepted a column that does not exist'
    original = Q.QUANTITIES
    try:
        Q.QUANTITIES = original + (ghost,)
        findings = era.findings()
    finally:
        Q.QUANTITIES = original
    assert len(findings) == 1
    msg = findings[0]
    assert 'zz_ghost' in msg and 'a_column_no_vintage_ever_wrote' in msg
    assert 'capability' in msg and 'db_columns' in msg, \
        'the message must name both routes out, not just the problem'


def test_a_capability_that_does_not_exist_is_refused():
    """Naming a capability is the escape hatch; naming a FICTIONAL one is worse than
    naming none, because it reads as though the absence has been thought about."""
    ghost = Q.Quantity(
        key='zz_ghost2', label='ghost', axis_stem='ghost', unit=units.NONE,
        direction='lower', capability='no_such_capability',
        source=Q.Source(per_batch=('batch', 'a_column_no_vintage_ever_wrote')))
    original = Q.QUANTITIES
    try:
        Q.QUANTITIES = original + (ghost,)
        findings = era.findings()
    finally:
        Q.QUANTITIES = original
    assert len(findings) == 1
    assert 'not in SIM_CAPABILITIES' in findings[0]


def test_a_real_capability_satisfies_the_gate():
    cap = picking_data.CAP_BIN_LOG
    ghost = Q.Quantity(
        key='zz_ghost3', label='ghost', axis_stem='ghost', unit=units.NONE,
        direction='lower', capability=cap,
        source=Q.Source(per_batch=('batch', 'a_column_no_vintage_ever_wrote')))
    original = Q.QUANTITIES
    try:
        Q.QUANTITIES = original + (ghost,)
        assert era.findings() == []
    finally:
        Q.QUANTITIES = original


# ── the derivation the gate rests on ─────────────────────────────────────────────

def test_a_derived_frame_column_declares_the_columns_it_is_built_from():
    """`completion_rate` is `total_items / duration`, computed in `frames._bdf`, and is in
    no database.  Declaring the FRAME name as if it were a column is how a quantity comes
    to claim a read the schema layer cannot check — it would have been reported as a gap
    against a surface that was right all along.
    """
    thr = Q.BY_KEY['throughput']
    table, cols = thr.source.db_reads
    assert table == 'batch_stats'
    assert set(cols) == {'total_items', 'duration'}
    assert 'completion_rate' not in cols


def test_every_declared_db_column_actually_exists_in_the_surface():
    """The reverse of the gate: a `db_columns` entry naming nothing real would make the
    gate pass by describing a read that is not the read being made."""
    surface = compat.guaranteed_surface('sim_db')
    for q in Q.QUANTITIES:
        reads = q.source.db_reads
        if not reads:
            continue
        table, cols = reads
        assert table in surface, f'{q.key} reads unknown table {table!r}'
        for col in cols:
            assert col in surface[table], \
                f'{q.key} declares {table}.{col}, which is not in the guaranteed surface'


def test_a_quantity_with_no_database_source_asks_the_gate_nothing():
    """Series scalars come from a document the analysis itself wrote; cost rows come from
    the run's runtime metrics. Neither is a sim-DB read, and pretending otherwise would
    put every one of them behind a capability it does not need."""
    for key in ('reord_ms_per_unit', 'scoring_ms_per_unit', 'x_reord_vs_fifo',
                'pick_volume', 'task_duration'):
        assert era.requires_for(Q.BY_KEY[key]) is None


def test_the_frame_table_map_covers_every_source_kind_in_use():
    kinds = {q.source.per_batch[0] for q in Q.QUANTITIES if q.source.per_batch}
    assert kinds <= set(Q.FRAME_TABLE), f'unmapped frame source kinds: {kinds}'
    assert kinds, 'no quantity reads a frame at all — the gate would be vacuous'


# ── the runtime half, and why it is not here yet ─────────────────────────────────

def test_no_quantity_names_a_capability_yet_and_that_is_why_there_is_no_probe():
    """A runtime probe with no consumer is validated infrastructure nobody calls.

    The static gate above is the COMPLETE mechanism for today's quantity table, and the
    reason is structural rather than lucky: `EvalContext._verify_sim_dbs` already hard-
    fails on a sim DB whose schema is not vetted, so an archived run is either vetted (and
    every read above is inside the guaranteed surface, so it can answer) or unvetted (and
    the analysis refuses to start). The gap the runtime half would cover — a VETTED
    vintage missing a non-guaranteed column — opens the moment a quantity names a
    capability, and not before.

    So this test fails on that day, deliberately, with the design note attached. Do not
    delete it to make the build green: implement the probe (fold `capability.probe` into
    `_verify_sim_dbs`, memoise `ctx.capabilities`, check it in `requests.resolve_needs`
    behind an `EraUnmet` distinct from `Denied`, and surface an `[era] run summary` beside
    the `[access]` one), then rewrite this test to assert the probe runs.
    """
    named = [q.key for q in Q.QUANTITIES if q.capability]
    assert not named, (
        f'{named} now name capabilities, so the RUNTIME half of the era gate is needed: a '
        f'vetted vintage can lack a non-guaranteed column, and today nothing would notice '
        f'except as a missing figure. See this test\'s docstring for the design.')
