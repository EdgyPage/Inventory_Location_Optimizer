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


def test_the_two_module_level_declarations_partition_every_quantity_read():
    """`QUANTITY_READS` exists so the schema-compatibility sweep can SEE the declaration.

    A `Requires` reached only through a factory is invisible to that sweep and therefore
    never validated — the exact gap the sweep's own docstring describes.

    Since the first capability-gated quantity there are TWO objects, split on exactly the
    line the layer is built on: `QUANTITY_READS` is the unconditional half and must stay
    inside the guaranteed surface (the sweep enforces that), `GATED_READS` is the half that
    deliberately does not. Together they must still be the whole union — a read in neither
    is a read nothing validates, which is the failure both objects exist to prevent.
    """
    union: dict = {}
    for q in Q.QUANTITIES:
        req = era.requires_for(q)
        if req is None:
            continue
        for table, cols in req.tables.items():
            union.setdefault(table, set()).update(cols)
    declared: dict = {}
    for obj in (era.QUANTITY_READS, era.GATED_READS):
        for table, cols in obj.tables.items():
            declared.setdefault(table, set()).update(cols)
    assert declared == union, 'a quantity read is in neither declaration'
    # The unconditional half is version-free, and must stay that way.
    assert compat.validate(era.QUANTITY_READS) == []
    # The gated half is outside the surface BY DESIGN — if it ever validates clean the
    # vintages have converged, which is good news and means the capability names and the
    # runtime probe can be retired rather than left as machinery nobody needs.
    assert era.GATED_READS.tables, 'the gated half is empty; fold it back into one object'
    assert compat.validate(era.GATED_READS), (
        'every gated read is now inside the guaranteed surface — the vintages converged, '
        'so retire the capability names rather than keeping a probe that always passes')
    # And the RULE over the whole table: every gap is covered by a named capability.
    assert era.findings() == []


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
    gate pass by describing a read that is not the read being made.

    Checked against the DECLARED shape, not the guaranteed surface.  The guaranteed one is
    the intersection over every vetted vintage, so a column added today is absent from it
    by construction — asserting against it would forbid measuring anything new, which is
    the opposite of what this test is for.  Whether a run can SERVE the read is the gate's
    own question (guaranteed, or a named capability); whether the column EXISTS at all is
    this one's, and only the declared shape can answer it.
    """
    declared = picking_data.declared_sim_schema_shape()['tables']
    shape = {t: {c['name'] for c in spec['columns']} for t, spec in declared.items()}
    for q in Q.QUANTITIES:
        for table, cols in q.source.all_db_reads:
            assert table in shape, f'{q.key} reads unknown table {table!r}'
            for col in cols:
                assert col in shape[table], \
                    f'{q.key} declares {table}.{col}, which this build does not write'


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


# ── the runtime half ─────────────────────────────────────────────────────────────
# It opened on 2026-08-31, when the yard and demand-service quantities became the first to
# name capabilities.  The predecessor of these tests asserted that no quantity did, with
# the design for the probe in its docstring, and it fired exactly as written.
#
# The gap it covers is narrow and real: a VETTED vintage that carries a conditional table
# and no rows in it.  The static gate above cannot see that — it is a statement about the
# quantity table, not about a file — and nothing else would notice, because the only
# symptom is a figure that does not appear.


def test_every_named_capability_is_a_real_registry_key():
    """A capability naming nothing is worse than none: `era.findings` would pass it and the
    runtime probe would refuse the quantity forever, on every run."""
    for q in Q.QUANTITIES:
        if q.capability:
            assert q.capability in picking_data.SIM_CAPABILITIES, \
                f'{q.key} names capability {q.capability!r}, which no registry entry defines'


def test_a_capability_gated_quantity_is_refused_when_the_run_cannot_answer_it():
    """The probe REFUSES, and refuses under its own kind of sentinel.

    `EraUnmet` is distinct from `Denied` because the two call for different actions: a
    denial says a file was missing (re-run the stage), while this says the file is present
    and older than the measurement (re-run the SWEEP, or report the degraded form). One
    bucket for both would print the wrong instruction on every archived run.
    """
    from Optimization.Performance_Evaluations.core import requests as R
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY

    class _Ctx:
        def __init__(self, have):
            self._have = frozenset(have)

        def capabilities(self):
            return self._have

    ev = EVAL_BY_KEY['yard.fee']
    assert any(Q.BY_KEY[k].capability for k in ev.quantities), \
        'yard.fee no longer draws a capability-gated quantity; pick another evaluation'
    got = R.era_shortfall(_Ctx(()), ev)
    assert isinstance(got, R.EraUnmet) and isinstance(got, R.Denied)
    assert not got, 'the sentinel must stay FALSY — every caller truth-tests it'
    assert 'yard' in got.missing
    # ...and grants it the moment the run can answer.
    assert R.era_shortfall(_Ctx(('yard',)), ev) is None


def test_an_ungated_evaluation_never_pays_for_the_probe():
    """A preset of version-free quantities must not open a connection to learn nothing."""
    from Optimization.Performance_Evaluations.core import requests as R
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY

    class _Exploding:
        def capabilities(self):
            raise AssertionError('an ungated evaluation probed the run for capabilities')

    assert R.era_shortfall(_Exploding(), EVAL_BY_KEY['layout.travel']) is None


def test_the_run_end_summary_counts_era_skips_separately():
    """`[access]` counts INPUTS. An era shortfall arriving in that column would read as a
    pipeline fault on a run that is simply older than the measurement."""
    from Optimization.Performance_Evaluations.core import requests as R
    snap = R.tally_snapshot()
    assert 'era' in snap and 'denied' in snap and 'granted' in snap
