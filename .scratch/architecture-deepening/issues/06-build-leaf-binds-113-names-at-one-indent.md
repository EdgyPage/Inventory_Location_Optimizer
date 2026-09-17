# 06 - _build_leaf binds 113 names at one indent level

Type: refactor
Status: needs-triage

## Context

`Optimization/simdriver/strategy_runner.py:1127-2851` is one function, 1,724 lines:

- `1170-1978` -- the assembly, 808 lines of straight-line construction
- `1979-2083` `_replenish` (nonlocal x7), `2084-2093` `_note_triggered` (x1)
- `2095-2691` `_step`, **596 lines** (nonlocal x13 at `2099-2101`)
- `2693-2845` `_finish` (nonlocal x11)

**113 distinct names are bound at that one indent level.** The recent extraction arc removed 31;
~82 remain, including `mgr`, `warehouse`, `inventory`, `affinity`, `strat`, `ctx`, `reloader`,
`bin_rec`, four crews, `_dock`, `_space_tl`, `pool`, `site`, three clocks and thirteen buffers.

**The smell is in plain sight at `1974-1978`.** Five variables -- `_late`, `_day_end`, `_put_base`,
`_batch_early`, `triggered` -- exist at assembly scope for no reason except that `nonlocal` needs
them bound there. Their lifetime is one batch; their scope's lifetime is one arm.

**Nothing sits between "the whole arm" and "a 100-line state object".** The only entry into `_step`
is `_run_strategy_worker(args)` with a 42-key payload, so every behavioural test of the batch body
is an e2e harness that hand-assembles a run tree: `test_coupled_unit_e2e.py` (673 lines),
`test_receiving_e2e.py` (447), `test_standing_yard_e2e.py` (373), `test_production_hours_e2e.py`
(286), `test_channel_runner_smoke.py` (291). The last says it out loud at `:37-46` -- these
harnesses duplicate the driver's own assembly because the assembly has no callable seam.

Two invariants were already hoisted to module level so a unit test could reach them --
`_check_declared_crew` (`:686`) and `_check_site_crews` (`:732`), with the docstring at `:729`
saying so. That is the right instinct applied to two of roughly a dozen.

## What to build

Split where construction stops and stepping starts (~`1978`). `Optimization/simdriver/leaf_assembly.py`:

- **`ArmAssembly.build(payload, scope) -> ArmAssembly`**, whose attributes are the ~40 durable
  names. The uid-block chaining (`1586-1600`, `1636-1640`), the crew checks, the resume plan and
  the stock-mode branch become methods on it.
- **`BatchState`** -- the five batch-lifetime names.

`_build_leaf` becomes ~30 lines: build the assembly, make a `BatchState`, close over both.

**Do NOT split it into `_build_inventory()` / `_build_warehouse()` / `_build_crews()` as free
functions taking twenty parameters each.** That moves complexity rather than concentrating it and
fails the deletion test. The win is the object.

## Why the extraction arc is precedent, not duplication

`SectionTimers`, `CheckpointWindow`, `ShiftLedger` and `AuditLedgers` each turned an undocumented
ordering rule into an interface -- `ShiftLedger`'s test sabotages the wrong call order to prove it
is visible, and `SectionTimers`' "a total always includes the open window" retired a real defect
class. Deleting any of them concentrates complexity back here. They stopped at 31 of 113 names and
never touched the assembly that creates them.

## Verification

- An `ArmAssembly` unit test that costs a payload dict and an assertion -- and check whether
  `test_actor_uid_blocks.py`, `test_site_dock_builder.py` and `test_declared_once.py` can stop
  reconstructing what the assembly does.
- `test_calltree_anchors.py` names the exact `SECTION_MAP` entry to fix when a hot-path symbol
  moves. Gate 10 every commit.
- Gates 1, 2 (mandatory for any new import edge), 3, 7, 10.
- `strategy_runner.py` carries `.scratch/architecture-drift/issues/02`. Attribute on the
  assertion, against the phase-0 baseline -- never on the failure count.
