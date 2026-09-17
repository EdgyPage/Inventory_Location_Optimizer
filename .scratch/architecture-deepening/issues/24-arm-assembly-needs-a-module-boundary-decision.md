# 24 - ArmAssembly needs a module-boundary decision first

Type: refactor
Status: open
Blocked by: -

Split from ticket 06, whose `BatchState` half landed 2026-09-17. This is the 810-line
assembly.

## The decision this needs before any code moves

Ticket 06 specified `Optimization/simdriver/leaf_assembly.py`. The assembly calls a dozen
module-level helpers that live in `strategy_runner.py` -- `_check_declared_crew`,
`_check_site_crews`, `_timed_build`, `_gain_bundle_for`, `_POOL_FACTORIES`, `_SiteDock` and
more. A new module either:

- **drags them with it**, a cascade nobody has scoped (`_gain_bundle_for` alone reaches
  `Inbound.gain` and `Optimization.config.strategies`); or
- **imports them back**, which is a CYCLE -- and this runs in a spawn pool where every job
  re-imports the whole source tree, so an import-order problem is a worker dying during
  re-import rather than a clean failure at the top.

The third option -- `ArmAssembly` as a class inside `strategy_runner.py` -- gets the whole win
of the ticket (one object instead of ~40 durable locals, the closures reading `asm.x`, a unit
test that costs a payload dict) and gives up only the file split. **That is probably the right
call**, and it should be MADE rather than inherited from the ticket's first sketch.

## The two mechanical halves, once that is decided

Both are computable rather than judged, which is the good news:

1. **Durable vs temporary.** A name bound at `_build_leaf`'s top indent is DURABLE iff one of
   the closures (`_replenish`, `_note_triggered`, `_step`, `_finish`, `_shift_close_out`)
   reads or writes it. Everything else stays a local of `build()`. Straight off the AST.
2. **The rename.** `mgr` -> `asm.mgr` across ~900 lines of closure body. **Not a regex** --
   ticket 06's much smaller rename used word boundaries and still corrupted two English
   comments, with nothing failing. The safe form is an AST walk that collects the exact
   `(lineno, col_offset)` of every `Name` node whose id is in the durable set, then rewrites
   those positions bottom-up. Comments and strings are untouched by construction.

## Verification

- `run_digest.py` DB-row neutrality. This is the driver's hot path and its only behavioural
  test surface is the e2e tier, so the digest is the instrument that can actually fail.
- The measurement ticket 06 used, taken the same way: names bound at the top indent,
  `nonlocal` statements, `nonlocal` names. 145 / 4 / 22 today.
- An `ArmAssembly` unit test that costs a payload dict and an assertion -- and check whether
  `test_actor_uid_blocks.py`, `test_site_dock_builder.py` and `test_declared_once.py` can stop
  hand-assembling a run tree to reach it.
- Gates 1, 2, 10.
