"""semantics — what a column MEANS, declared once, machine-checkable.

The repo has hit at least twelve distinct classes of column-semantic misread — a stamp read
as a span, a level summed as a flow, packs added to pieces, wall-clock seconds ratioed
against sim seconds — and every one failed silently, because the semantics lived in DDL
comment prose that no gate executes (see `.scratch/inbound-groundwork/assets/column-audit.md`
while the effort is in flight; the incident ledger will move to a design doc when it closes).
This module is the structured home those facts never had.

# ── the vocabulary ────────────────────────────────────────────────────────────────

Nine KINDS, defined in root `CONTEXT.md`'s Measurement section and repeated here only as
constants: STAMP (a point on a clock), SPAN (elapsed), LEVEL (standing, re-measured; never
summed across snapshots), FLOW (additive at the row's grain), COUNT (a discrete flow), RATE
(flow / span; the denominator is part of the meaning), SCORE (policy-relative), SHARE
(proportion), LABEL (identity / ordinal / enum).

Each column declares kind + unit + grain always; clock, account, per, null-meaning, a
logical name and a scar note when they apply.  A column whose kind depends on a sibling's
VALUE (`carryover.qty` by `reason`) declares a `ByDiscriminator` — the case no comment
convention can check, and the sharpest single argument for this module existing.

# ── where declarations live ───────────────────────────────────────────────────────

Beside the DDL, keyed exactly as `Family.declared_shape()` keys its tables — so the
completeness gate is a dict diff with nothing to drift.  A family registers its table with
`register(family_name, semantics, covered)`; `covered` names the tables whose tagging is
COMPLETE and therefore gated (`Tests/architecture/test_column_semantics.py`).  The end state
is every table of every family covered — "leave no remainder" — reached family by family.

# ── what this module deliberately does NOT do ─────────────────────────────────────

Unit CONVERSION.  `SECONDS_PER_HOUR` lives in `Warehouse/kernel/timeline` with one blessed
import chain through the analysis suite's `common/units.py`, and this module is a
stdlib-only leaf that imports neither.  The guards here CHECK (may you sum this? are these
the same clock? the same unit of account?); turning a checked value into hours stays where
the one divisor already lives.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── the nine kinds, and the axes ──────────────────────────────────────────────────

STAMP, SPAN, LEVEL, FLOW, COUNT, RATE, SCORE, SHARE, LABEL = (
    'stamp', 'span', 'level', 'flow', 'count', 'rate', 'score', 'share', 'label')
KINDS: tuple = (STAMP, SPAN, LEVEL, FLOW, COUNT, RATE, SCORE, SHARE, LABEL)

#: Additive kinds — the only ones `sum_of` will total across rows.  SPAN is here on
#: purpose: spans of WORK add (total labour is SUM of per-event durations — the
#: task_makespan invariant), and person-seconds vs wall-seconds is the reader's per-what.
#: A LEVEL never adds: it re-counts the same standing quantity every snapshot.
ADDITIVE: tuple = (FLOW, COUNT, SPAN)

#: Clocks.  The same word "seconds" appears on all three and they are mutually
#: incommensurable (incident class 11): sim-modeled time, wall-clock compute time, and the
#: batch-denominated countdown the legacy lead queue ticks in.
SIM, WALL, BATCHES = 'sim', 'wall', 'batches'
CLOCKS: tuple = (SIM, WALL, BATCHES)

#: Units of account for counts of goods (incident class 7): a pack is what a person lifts,
#: a piece is the merchandise inside.  2.00x apart on a measured batch; never added.
PACKS, PIECES = 'packs', 'pieces'

#: Units that denote time and therefore REQUIRE a `clock` axis on the declaring column.
TIME_UNITS: tuple = ('s', 'batches')


class SemanticsError(Exception):
    """A read refused because the declared semantics forbid it. The message carries the
    incident history when the column has one — that is what makes the refusal auditable."""


@dataclass(frozen=True)
class Col:
    """One column's declared semantics.  kind + unit + grain are mandatory; the rest are
    conditionally mandatory (the gate demands them when they apply) — nothing is
    optional-and-silent."""

    kind: str
    unit: str
    grain: str                       # the per-what of one row: 'batch', 'arm', 'row', ...
    clock: str | None = None         # REQUIRED when unit is a time unit
    account: str | None = None       # REQUIRED on counts of goods (packs vs pieces)
    per: str | None = None           # a RATE's denominator, part of its meaning
    null_means: str | None = None    # REQUIRED when the column is nullable: absent != 0
    logical: str | None = None       # the honest name at the logical layer; physical frozen
    pair: str | None = None          # the plan/actual twin this column pairs with
    space: str | None = None         # an id column's space ('crew-unique', 'crew-local', ...)
    note: str | None = None          # the scar: the incident this tag exists to prevent

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f'unknown kind {self.kind!r}; the vocabulary is {KINDS}')
        if self.unit in TIME_UNITS and self.kind in (STAMP, SPAN, RATE)                 and self.clock is None:
            raise ValueError(f'a {self.unit!r}-denominated {self.kind} must declare its '
                             f'clock (sim / wall / batches are not the same instrument); '
                             f'a COUNT of batches is a count, not a time')
        if self.clock is not None and self.clock not in CLOCKS:
            raise ValueError(f'unknown clock {self.clock!r}; known: {CLOCKS}')


@dataclass(frozen=True)
class ByDiscriminator:
    """A column whose kind depends on a sibling column's VALUE in the same row.

    `carryover.qty` is the canonical case: three `reason` values are re-emitted LEVELS and
    four are FLOWS, sharing one physical column under one PK.  Row-free aggregation over
    such a column is not defined, and every guard here says so rather than guessing.
    """
    column: str
    cases: dict = field(default_factory=dict)   # discriminator value -> Col
    note: str | None = None

    def __post_init__(self):
        if not self.cases:
            raise ValueError(f'a discriminator over {self.column!r} with no cases '
                             f'declares nothing')


# ── the registry ──────────────────────────────────────────────────────────────────

_REGISTRY: dict = {}    # family name -> (semantics, covered)


def register(family: str, semantics: dict, covered: tuple) -> None:
    """Declare a family's column semantics and which tables are COMPLETELY tagged.

    `semantics` is {table: {column: Col | ByDiscriminator}}, keyed exactly as
    `Family.declared_shape()['tables']` keys them.  `covered` is the gate's scope: every
    column of a covered table must be tagged, and every tag must name a real column.
    """
    for t in covered:
        if t not in semantics:
            raise ValueError(f'{family}: {t!r} is covered but has no semantics at all')
    _REGISTRY[family] = (dict(semantics), tuple(covered))


def semantics_for(family: str) -> tuple:
    """(semantics, covered) for a registered family; raises on an unknown one."""
    if family not in _REGISTRY:
        raise KeyError(f'no semantics registered for family {family!r}; '
                       f'known: {sorted(_REGISTRY)}')
    return _REGISTRY[family]


def resolve(family: str, table: str, column: str, row=None):
    """The tag for one column, accepting physical OR logical names.

    A `ByDiscriminator` needs `row` (a mapping) to pick its case; without one it raises,
    because row-free semantics for such a column do not exist.
    """
    sem, _ = semantics_for(family)
    cols = sem.get(table)
    if cols is None:
        raise KeyError(f'{family}.{table}: no semantics declared for this table')
    tag = cols.get(column)
    if tag is None:
        for phys, c in cols.items():
            if isinstance(c, Col) and c.logical == column:
                return c
        raise KeyError(f'{family}.{table}.{column}: not a declared column or logical name')
    if isinstance(tag, ByDiscriminator):
        if row is None:
            raise SemanticsError(
                f'{table}.{column}: kind depends on {tag.column!r} in the same row; '
                f'row-free semantics do not exist for it'
                + (f'  [{tag.note}]' if tag.note else ''))
        return tag.cases[row[tag.column]]
    return tag


# ── the guards (checking only — conversion lives with the one divisor) ────────────

def sum_of(rows, family: str, table: str, column: str) -> float:
    """Total `column` across `rows` — refused unless its kind is additive.

    The additive statistic for a LEVEL is the count of rows non-zero, never the sum: a
    level re-counts the whole standing quantity every snapshot.
    """
    sem, _ = semantics_for(family)
    tag = sem[table][column]
    if isinstance(tag, ByDiscriminator):
        raise SemanticsError(
            f'SUM({table}.{column}) refused: kind depends on {tag.column!r} — '
            f'aggregate per-{tag.column}, never across the table'
            + (f'  [{tag.note}]' if tag.note else ''))
    if tag.kind not in ADDITIVE:
        raise SemanticsError(
            f'SUM({table}.{column}) refused: kind={tag.kind.upper()} is not additive'
            + (f'  [{tag.note}]' if tag.note else ''))
    return sum(r[column] for r in rows)


def check_add(family: str, table: str, a: str, b: str) -> None:
    """May these two columns be added element-wise? Same unit AND same unit of account."""
    sem, _ = semantics_for(family)
    ta, tb = sem[table][a], sem[table][b]
    for t in (ta, tb):
        if isinstance(t, ByDiscriminator):
            raise SemanticsError(f'{table}.{a}+{b} refused: a discriminated column cannot '
                                 f'be added row-free')
    if ta.unit != tb.unit or ta.account != tb.account:
        raise SemanticsError(
            f'{table}.{a} + {table}.{b} refused: units of account differ '
            f'({ta.unit}/{ta.account} vs {tb.unit}/{tb.account}) — packs are not pieces')


def check_ratio(family: str, table_a: str, a: str, table_b: str, b: str) -> None:
    """May a/b form a ratio? Same clock when both are time — the same word "seconds" on
    different instruments cost a day and a wrong committed claim (incident class 11)."""
    sem, _ = semantics_for(family)
    ta, tb = sem[table_a][a], sem[table_b][b]
    for t in (ta, tb):
        if isinstance(t, ByDiscriminator):
            raise SemanticsError('a discriminated column cannot enter a row-free ratio')
    if ta.clock is not None and tb.clock is not None and ta.clock != tb.clock:
        raise SemanticsError(
            f'{table_a}.{a} / {table_b}.{b} refused: clocks differ ({ta.clock} vs '
            f'{tb.clock}) — same word "seconds", different instruments')


# ── use-assertions: a consumer declares HOW it reads, checked at declaration time ──

def validate_uses(family: str, label: str, uses: dict) -> list:
    """Refusal clauses for a consumer's declared reads, `Requires`-style.

    `uses` is {'table.column': 'sum' | 'ratio' | 'read'}.  Returned clauses are readable
    sentences naming the consumer, so a failure reads like a review comment rather than a
    stack trace.  Empty list = every declared use is legal.
    """
    sem, _ = semantics_for(family)
    clauses = []
    for key, how in uses.items():
        table, col = key.split('.')
        tag = sem.get(table, {}).get(col)
        if tag is None:
            clauses.append(f'[{label}] {key}: not a declared column')
        elif isinstance(tag, ByDiscriminator):
            if how != 'read':
                clauses.append(f'[{label}] {key}: declares {how!r} but kind depends on '
                               f'{tag.column!r} — declare per-{tag.column} uses instead')
        elif how == 'sum' and tag.kind not in ADDITIVE:
            clauses.append(f'[{label}] {key}: declares SUM over a {tag.kind.upper()}'
                           + (f'  [{tag.note}]' if tag.note else ''))
    return clauses


# ── the completeness gate ─────────────────────────────────────────────────────────

def check_completeness(shape_tables: dict, family: str) -> list:
    """Problems between a family's declared shape and its declared semantics.

    `shape_tables` is `Family.declared_shape()['tables']` verbatim — {table: {'columns':
    [{'name', 'type', 'notnull', 'pk'}, ...], ...}} — consumed directly so there is no
    parallel column list to drift.  For every COVERED table: every column tagged, no tag
    naming a vanished column, null-meaning present wherever the DDL allows NULL, and a
    clock wherever the unit is time.  Empty list = the gate passes.
    """
    sem, covered = semantics_for(family)
    problems = []
    for table in covered:
        spec = shape_tables.get(table)
        if spec is None:
            problems.append(f'{table}: covered but absent from the declared shape')
            continue
        cols = {c['name']: c for c in spec['columns']}
        tags = sem.get(table, {})
        for name, meta in cols.items():
            tag = tags.get(name)
            if tag is None:
                problems.append(f'{table}.{name}: untagged')
                continue
            variants = tag.cases.values() if isinstance(tag, ByDiscriminator) else (tag,)
            for v in variants:
                if meta['notnull'] == 0 and meta['pk'] == 0 and v.null_means is None:
                    problems.append(f'{table}.{name}: nullable but declares no '
                                    f'null-meaning (absent is not zero)')
                if v.unit in TIME_UNITS and v.kind in (STAMP, SPAN, RATE)                         and v.clock is None:
                    problems.append(f'{table}.{name}: time unit with no clock')
        for name in tags:
            if name not in cols:
                problems.append(f'{table}.{name}: tagged but no such column in the shape')
    return problems
