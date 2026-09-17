# 14 - a measured span has four spellings and no join

Type: refactor
Status: needs-triage
Blocked by: 07

## Context

`Optimization/simdriver/section_timers.py:38-62` documents the problem against itself:

> `totals()` emits the THIRD column -- the worker RESULT-DICT keys -- not the DB column names. The
> result dict is mapped onto columns positionally by `runtime_metrics.record_arm` ... **so the two
> are related only by that hand-written call.**

The chain: `SectionTimers.SECTIONS` (12 entries) -> `totals()` -> result-dict keys `t_<name>` ->
`strategy_runner._finish`'s return (`:2807-2845`, two of them `**audit.totals()` /
`**timers.totals()` expansions) -> pickled across the process seam ->
`Optimization/persistence/runtime_metrics.py:150-206` `record_arm`, a 32-column
`INSERT OR REPLACE` built from `res.get('t_sample', 0.0) -> smpl_s` style calls.

**A section added to `SECTIONS` flows automatically into `totals()` and then silently vanishes at
`record_arm`** -- writing nothing, raising nothing.

The team already knows the hazard and applies the discipline to **2 of 32 columns**: the two setup
columns at `:199-202` are deliberately NOT coerced, because "a worker that did not measure the
precompute must write NULL, or the analysis cannot tell 'no map to build' from '0.0 s'". Every
other read is `res.get(key, 0.0) or 0.0`.

The DB column names are a hard contract -- `section_timers.py:63-66` records that a rename shifts
the `runtime_metrics` schema id and breaks archived rows' comparability with themselves -- so the
mapping cannot simply be `t_<name> -> <name>_s`.

Nothing checks that `SECTIONS` and the `runtime` DDL are in step.
`Tests/architecture/test_digest_surface.py` is in the tenth gate precisely because these
instruments "fail SILENTLY and in the direction of looking healthy" (`CLAUDE.md` §1), and
`run_digest.py` was dead twice for this reason.

## What to build

Make `SECTIONS` a three-column table -- `(section, result_key, db_column)` -- and have `record_arm`
build its column list and value tuple from that table plus a small table for the non-timer columns.
A section then declares its three spellings once.

Keep the NULL-vs-0.0 distinction as a per-row property rather than two hand-written exceptions:
a column declares whether an absent measurement is `None` or `0.0`, and why.

## Verification

- One test asserting every declared column exists in the `runtime` DDL **and** every DDL section
  column is declared. This is the shape `test_calltree_anchors.py` already applies to
  `SECTION_MAP`, extended one hop.
- Exercise it: run an arm, assert each declared section produced a non-NULL value where it should
  have. Memory `symbol-table-relationship-not-verified-by-symbols` -- a gate that resolves NAMES
  cannot catch a wrong RELATIONSHIP; the fix shape is "exercise it and assert it produced
  something".
- Any DDL movement rides the schema pipeline. Gate 10 (which covers `test_digest_surface.py`).

## Comments

Natural follow-on to ticket 07 -- both are about the seam between what a worker measured and what
lands in a row.
