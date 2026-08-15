"""capability.py — negotiating for data that only SOME vetted schemas carry.

`compat.py` splits a family's tables into a guaranteed surface (present in every vetted shape, so
a consumer may read it unconditionally) and a conditional one. This module is how the conditional
half is reached: **probe, then degrade with a recorded caveat.** Never assume, and never quietly
return something plausible.

Two things make that more than a `try/except`:

  * **A table that EXISTS can still be empty, and empty is common.** `aisle_metrics` and
    `reorder_queue` are in every vetted shape but are only written by strategies that maintain
    that state. Measured across two archived what-if cells: `aisle_metrics` carries rows in 52 of
    60 arms, `reorder_queue` in 68 of 166 — so a third of arms have the table and none of the
    data. A probe that checks for the TABLE rather than for ROWS reports a capability that yields
    nothing, and the UI then renders 0.0 as though it were a measurement.
  * **Sources are not interchangeable.** Three different tables can answer "which bins were
    occupied at batch N" with different fidelity AND at different instants of a batch. Picking the
    best available one is only safe if the answer carries which one it was, so a number cannot be
    read without its caveat.

This module owns the TYPE and the PROBE. It does not own any particular family's capabilities:
those live next to the DDL that defines the tables (see `Picking_Data.SIM_CAPABILITIES`), because
`Schema/` is the stdlib-only leaf and must not learn what a warehouse is.

Before this existed the same idea was hand-rolled twice, independently, and could not be shared:
`Visualization/readers/protocol.py` (`CAP_*` + `SqliteSimReader.capabilities`) and
`Diagnostics/replay_run.py` (`_SOURCES` + `_has_rows` + `_occupancy`). `context/architecture.yml`
forbids `optimization -> visualization`, so the analysis layer could not reuse either and had no
way to negotiate at all — it could only hard-fail.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


class CapabilityError(Exception):
    """Base for capability-negotiation failures."""


class NoSourceAvailable(CapabilityError):
    """Nothing in the ladder is available, and the caller declared no acceptable fallback."""


# ── the type ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, eq=False)
class Capability:
    """One optional data source, and what it is worth.

    `exact`, `phase` and `caveat` are not documentation — they are the payload. A consumer that
    selects a source is expected to carry them into whatever it emits, so the number cannot be
    read without them. `Diagnostics/replay_run.py` copies them verbatim into its exported JSON;
    that is the pattern.

    `table=None` marks a capability whose availability cannot be answered by a row probe (a
    sibling file exists; a derived cache is fresh). The caller supplies those to `probe(extra=...)`
    — the negotiation stays uniform even when the evidence is not a table.
    """
    name: str
    table: str | None
    exact: bool
    phase: str                          # WHICH INSTANT of a batch this source describes
    caveat: str                         # what a reader must be told; '' only when exact
    columns: tuple = field(default_factory=tuple)

    def __post_init__(self):
        if not self.exact and not self.caveat:
            raise ValueError(
                f'capability {self.name!r} is inexact but carries no caveat. An approximate '
                f'source without a recorded caveat is the failure this module exists to prevent.')


# ── probing ──────────────────────────────────────────────────────────────────────

def has_rows(con: sqlite3.Connection, table: str, run_id: int | None = None) -> bool:
    """Does `table` exist AND carry at least one row (for `run_id`, when given)?

    The `run_id` filter is not decoration: an arm that was resumed or interrupted can leave a
    table created-but-empty for the run being replayed, and an unfiltered probe would then select
    a source that folds to nothing.

    A missing table and an empty one both answer False, deliberately — to a consumer deciding
    whether it can draw a panel, "the schema predates this" and "this arm never wrote any" are
    the same fact.
    """
    sql = (f'SELECT 1 FROM "{table}" LIMIT 1' if run_id is None
           else f'SELECT 1 FROM "{table}" WHERE run_id=? LIMIT 1')
    try:
        return con.execute(sql, () if run_id is None else (run_id,)).fetchone() is not None
    except sqlite3.OperationalError:                  # no such table -> this run predates it
        return False


def probe(con: sqlite3.Connection, capabilities, run_id: int | None = None,
          extra=()) -> frozenset:
    """Names of the capabilities this database actually HAS.

    `extra` names capabilities the caller has already established by other means (a keyframe
    sidecar exists; a derived cache is fresh) — anything whose `table` is None.
    """
    out = set(extra)
    for cap in capabilities:
        if cap.table is None:
            continue                                  # caller's business, via `extra`
        if has_rows(con, cap.table, run_id):
            out.add(cap.name)
    return frozenset(out)


def best(available, ladder):
    """The first capability in `ladder` whose name is available, else None.

    `ladder` is ordered BEST-FIRST by the consumer, because "best" is a question about what is
    being asked: an exact end-of-batch source beats an approximate one for occupancy, and the
    ordering is not a property of the capabilities themselves.
    """
    for cap in ladder:
        if cap.name in available:
            return cap
    return None


def require(available, ladder, *, what: str):
    """`best`, but raising `NoSourceAvailable` naming every source that was tried.

    For a consumer that cannot degrade any further and must fail loudly rather than emit
    something shaped like an answer.
    """
    cap = best(available, ladder)
    if cap is None:
        raise NoSourceAvailable(
            f'no source available for {what}; tried {", ".join(c.name for c in ladder)}. '
            f'This run carries none of them.')
    return cap


def provenance(cap) -> dict:
    """The caveat payload to attach to whatever a consumer emits from `cap`.

    Returned as plain JSON-serializable data so it can go straight into an API response, an
    exported JSON file, or a figure caption without a converter.
    """
    return {'source': cap.name, 'exact': cap.exact, 'phase': cap.phase, 'caveat': cap.caveat}
