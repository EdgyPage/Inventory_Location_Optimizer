"""era — can this run's data answer the quantities the suite declares?

The publish loop has one route it cannot automate: a stakeholder asks for a number the
simulation never wrote down.  That cannot be backfilled — schema identity is derived from
shape and archived files are never rewritten — so the only honest answers are a new sweep
on a vintage that records it, or a recorded caveat and the degraded form.

What made that dangerous is that nothing said which route you were on.  A quantity reading
a column only SOME vetted vintages carry produces a figure on the runs that have it, no
figure at all on the ones that do not, and an INFO line either way.

## The rule

Straight out of `Schema/compat.py`'s own docstring, applied to the quantity table rather
than reinvented beside it.  Every quantity must satisfy EITHER

    compat.validate(requires) == []   inside `guaranteed_surface` -> version-free, done
    q.capability names a real key     outside it -> a CAPABILITY, and it is SAID

Neither is a failure.  Being NEITHER is: it means a figure will be silently absent, or
silently wrong, on every archived run made before the column existed.

## Why this is a separate module

`core/quantities.py` is stdlib-only on purpose — that is what lets a test, `ingest.py` or a
schema tool read the quantity table without importing the analysis package.  The gate needs
`Schema.compat` and the sim-DB family registration, so it lives here and imports there.
The layering also reads correctly: the quantity table describes MEASUREMENTS; whether a
given archive can serve one is a schema question.

## The runtime half, and why it is not here

`EvalContext._verify_sim_dbs` already hard-fails on a sim DB whose schema is not vetted, so
an archived run is either vetted — and every read below is inside the guaranteed surface,
so it can answer — or the analysis refuses to start.  The gap a runtime probe would cover
(a VETTED vintage missing a non-guaranteed column) opens the moment a quantity names a
capability, and not before.  `Tests/architecture/test_data_era_gate.py` fails on that day
with the design attached; a probe with no consumer is validated infrastructure nobody
calls, and this repo already has one of those.
"""
from __future__ import annotations

import Optimization.persistence.Picking_Data as _picking_data   # registers the sim_db family
from Schema import compat

from Optimization.Performance_Evaluations.core import quantities as _q


def _table_columns(gated: bool) -> dict:
    """{table: (columns,)} over the quantities that do (or do not) name a capability."""
    tables: dict = {}
    for q in _q.QUANTITIES:
        if bool(q.capability) is not gated:
            continue
        for table, columns in q.source.all_db_reads:
            tables.setdefault(table, set()).update(columns)
    return {t: tuple(sorted(c)) for t, c in sorted(tables.items())}


#: THE declaration, at module level so the schema-compatibility sweep can see and validate
#: it.  The union of every UNCONDITIONAL sim-DB read the quantity table makes: if this is
#: inside the guaranteed surface then so is each part, which is what makes that half of the
#: table version-free by construction rather than by inspection.
#:
#: The capability-gated reads are deliberately NOT here, and the split is the mechanism
#: rather than a loophole.  A declared consumer is a promise that EVERY vetted vintage can
#: serve it; a gated read is the opposite promise — that some cannot, said out loud, with a
#: capability naming what is needed and a runtime probe refusing the render when the run
#: lacks it (`requests.era_shortfall`).  Folding the two into one object would mean either
#: weakening the sweep for every consumer in the repo, or refusing to measure anything the
#: archive predates.  `GATED_READS` below keeps the other half visible; `findings()`
#: re-checks per quantity, so the MESSAGE can name which one.
QUANTITY_READS = compat.Requires(
    family='sim_db',
    label='Performance_Evaluations quantity table',
    tables=_table_columns(gated=False))

#: The other half: reads that some vetted vintage cannot serve, each behind a named
#: capability.  Declared as data — not validated against the guaranteed surface, because
#: being outside it is the whole point — so a reader can see the conditional surface the
#: suite depends on without deriving it, and so a test can assert it is non-empty rather
#: than letting the split quietly collapse back into one.
GATED_READS = compat.Requires(
    family='sim_db',
    label='Performance_Evaluations quantity table (capability-gated)',
    tables=_table_columns(gated=True))


def requires_for(q):
    """The `Requires` one quantity's sim-DB read amounts to, or None for no read.

    DERIVED, not declared: the table comes from the frame builder the source names and the
    columns from the source itself, so a quantity cannot describe a read it does not make.
    A quantity with no per-batch source reads no database — its numbers come from a series
    document the analysis wrote, or from the run's own cost rows.

    `all_db_reads`, not `db_reads`: a quantity whose denominator lives in another table
    (`missed_share`) makes two reads, and validating only the first would let the second
    slip past the gate — which is the whole failure this module is the guard against.
    """
    reads = q.source.all_db_reads
    if not reads:
        return None
    tables: dict = {}
    for table, columns in reads:
        tables.setdefault(table, set()).update(columns)
    return compat.Requires(family='sim_db', label=f'quantity {q.key}',
                           tables={t: tuple(sorted(c)) for t, c in tables.items()})


def findings() -> list:
    """Quantities that read something not every vetted sim vintage carries, unnamed.

    THE STATIC RULE — no database, runnable in CI.  Empty when honest.
    """
    out = []
    for q in _q.QUANTITIES:
        req = requires_for(q)
        if req is None:
            continue
        gaps = compat.validate(req)
        if not gaps:
            continue
        if q.capability and q.capability in _picking_data.SIM_CAPABILITIES:
            continue
        if q.capability:
            out.append(f'{q.key} names capability {q.capability!r}, which is not in '
                       f'SIM_CAPABILITIES')
            continue
        out.append(
            f'{q.key} reads {"; ".join(gaps)} — outside the guaranteed sim_db surface, and '
            f'it names no capability. Either the read is version-free and the surface '
            f'needs re-deriving, or some vetted vintage cannot answer this quantity and '
            f'that has to be said out loud: name a SIM_CAPABILITIES key, or declare the '
            f'columns the frame actually builds it from in Source.db_columns.')
    return out
