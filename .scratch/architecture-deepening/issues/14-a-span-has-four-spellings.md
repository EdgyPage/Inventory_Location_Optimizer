# 14 - a measured span has four spellings and no join

Type: refactor
Status: resolved
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


---

## RESOLVED 2026-09-17  (6692685a)

`runtime_metrics.SPANS` is the join. A span names its accumulator key, result-dict key, DB
column and stacked-graph label once; `record_arm` builds its INSERT from the table.

### Three tables become derivations, each reproducing its original exactly

| table | was | now |
|---|---|---|
| `SectionTimers.SECTIONS` | 12 names written out | `tuple(s.section for s in SPANS)` |
| `SectionTimers.COLUMNS` | `{'p1': 'p1_s', 'p2': 'p2_s'}` | the spans whose result key is not `t_<section>` |
| `runtime_metrics.SECTIONS` | 7 `(column, label)` pairs | the spans with a label |

The third is the one that was quietly dangerous. It is a PARTITION for the stacked graph and
its own comment warns that adding an overlay double-counts. `label is None` now MEANS overlay,
so that is a derivation rather than a rule a reviewer has to remember.

The declaration ORDER was chosen to reproduce `SectionTimers.SECTIONS` exactly -- its own order,
matching neither the log line nor the DDL -- and the new test pins it as a literal.

### THE TABLE DOES NOT LIVE WHERE THE TICKET PUT IT

The ticket says "make `SECTIONS` a three-column table", i.e. extend the one in
`section_timers`. That cannot work: `record_arm` would then have to read it, and
`opt_persistence -> opt_simdriver` is a declared boundary -- *"storing results must not depend
on orchestration"*. It lives with the DDL instead, which is also right on merit: the DB column
is the hard contract (renaming one shifts the runtime_metrics schema id for a relabelling and
breaks archived rows' comparability with themselves), and a contract belongs beside the thing it
constrains. `section_timers` owns the accumulator key and reads the other three.

### The NULL-vs-0.0 rule is a declaration now

`absent` per column, as the ticket asked. The setup spans keep NULL: "not measured" and
"measured as zero" are different claims and telling them apart is the entire job of
`precomp_src`. `test_a_column_that_must_stay_NULL_stays_NULL` asserts BOTH directions -- an
unmeasured precompute writes NULL, and a *measured* 0.0 survives as 0.0 with
`precomp_src='inline'` beside it.

### Verification

| check | result |
|---|---|
| `Tests/unit` + `Tests/integration -k "not gpu"` | 3,220 passed / 2 skipped |
| `Tests/unit/test_runtime_span_table.py` | 13 tests, 2 of them sabotages |
| toy run vs baseline, `run_digest.py` | **IDENTICAL**, 136 arms |
| all ten gates | green |

**What the digest reaches here, precisely.** It hashes the runtime table's row IDENTITY and,
implicitly, its column list, so a column added, dropped or renamed would show -- which is this
change's risk class. It cannot reach the measured seconds: they are wall-clock and excluded by
design, and the exclusion comment says so in as many words. That is exactly why the new test
writes a row through `record_arm` with a distinct value per span and reads every column back.

The DDL is untouched and `declared_runtime_shape()` is byte-identical to HEAD's, checked rather
than assumed.

### What this unblocks

Nothing was waiting on 14. It was the last of the three tickets (07, 11, 14) about the seam
between what a worker measured and what lands in a row.
