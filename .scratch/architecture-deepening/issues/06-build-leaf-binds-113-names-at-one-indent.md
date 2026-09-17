# 06 - _build_leaf binds 113 names at one indent level

Type: refactor
Status: resolved

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


---

## Progress -- `BatchState` landed; `ArmAssembly` does NOT fit this ticket's shape (2026-09-17)

### What landed

`Optimization/simdriver/batch_state.py`. The five names this ticket calls "the smell in plain
sight" are one object, measured the same way before and after:

| | before | after |
|---|---|---|
| lines in `_build_leaf` | 1,729 | 1,725 |
| names bound at its top indent | 149 | 145 |
| `nonlocal` statements | 7 | 4 |
| names reached through `nonlocal` | 33 | 22 |

(149 rather than the ticket's 113 because this count includes names bound inside top-level
`if`/`for`/`with` bodies and the parameters. What matters is that both numbers came from the
same script.)

They were arm-scope for a LANGUAGE reason and nothing else: `_replenish` and `_step` are two
halves of one batch, they must share these values, and `nonlocal` can only rebind a name that
already exists in the enclosing scope. Five values whose lifetime is one batch therefore lived
in a scope whose lifetime is one arm. The halves now mutate fields, so nothing is rebound.

No `reset()` on the object, deliberately: every field is unconditionally re-seeded by
`_replenish` before `_step` can read it, so a reset would be a second writer of the same fact
that happens to be a no-op.

Digest **IDENTICAL**, 136 arms. `Tests/unit` + `Tests/integration` 3,161 passed.

### The rename was scoped and word-boundary matched, and it still hit prose twice

The pattern for `triggered` matched inside two English comments -- "hands back what this one
triggered" became "what this one bstate.triggered". Caught by reading the diff, not by a test.
Worth recording because the remaining half needs the same rename at fifty times the scale, and
nothing would have failed.

### Why `ArmAssembly` is NOT in this ticket, and what it actually needs

The ticket specifies `Optimization/simdriver/leaf_assembly.py` holding
`ArmAssembly.build(payload, scope)`. **That module boundary does not survive contact with the
code**, and finding that out is the useful part of this attempt:

1. **The durable/temporary split IS mechanical.** A name is durable iff one of the closures
   reads or writes it; the rest stay locals of `build()`. That comes off the AST rather than
   being judged, and `build()` then ends with `return cls(mgr=mgr, warehouse=warehouse, ...)`
   -- which is the record's field list, not "forty names at one indent".
2. **The rename is mechanical AND safe, but only with the right tool.** `mgr` -> `asm.mgr`
   across ~900 lines of closure body is not a regex job (see the prose hits above); it is an
   AST walk that collects the exact `(lineno, col_offset)` of every `Name` node and rewrites
   those positions bottom-up, touching no comment or string.
3. **The module boundary is the real blocker.** The 810 lines of assembly call a dozen
   module-level helpers in `strategy_runner.py` -- `_check_declared_crew`, `_check_site_crews`,
   `_timed_build`, `_gain_bundle_for`, `_POOL_FACTORIES` and more. A new module either drags
   those with it (a cascade nobody scoped) or imports them back, which is a CYCLE -- in a
   spawn pool, where every job re-imports the source tree, so that shows up as a worker dying
   during re-import rather than as a clean failure at the top.

So the remaining half is a judgement-heavy split of the driver, not a mechanical extraction,
and the first thing it needs is a decision this ticket assumed away: **what else moves to
`leaf_assembly.py`, or does `ArmAssembly` live in `strategy_runner.py`?** Ticket 24, rather
than an unfinished claim here.


---

## RESOLVED 2026-09-17 -- `ArmAssembly` landed, in `strategy_runner.py`

### The measurement

| | before | after |
|---|---|---|
| `_build_leaf` | 1,729 lines | **918** |
| names bound at its top indent | 149 | **11** |
| `nonlocal` statements in it | 7 | **0** |
| names reached through `nonlocal` | 33 | **0** |

The other 140 names are inside `_build_arm`, where nothing outside the construction can see
them. 76 of them are DURABLE -- a closure touches one -- and are the `ArmAssembly`'s fields;
the remaining 60 are construction temporaries that now genuinely are temporary.

### The module-boundary decision, made

The ticket specified `Optimization/simdriver/leaf_assembly.py`. **It does not go there**, and
ticket 24 (raised earlier today for exactly this question) is answered by the same finding
that raised it: the assembly calls a dozen module-level helpers in `strategy_runner.py`, so a
new module either drags them along in an unscoped cascade or imports them back as a cycle --
in a spawn pool where every job re-imports the source tree.

So `ArmAssembly` and `_build_arm` live in `strategy_runner.py`. That gives up the file split
and keeps everything the split was FOR: one object instead of 76 loose locals, closures that
read it instead of reaching back, no `nonlocal` anywhere, and a callable seam that costs a
payload dict.

**And `_build_arm` is a module-level function rather than a classmethod**, also deliberately:
its body sits at one indent today and sits at one indent there, so the diff is the move and
nothing else. As a classmethod all 810 lines would also gain four spaces, and a
whitespace-only change on 810 lines hides the real one from review.

### The rename was 380 tokens and it was NOT a regex

Ticket 06's own `BatchState` half corrupted two English comments with a word-boundary pattern
scoped to one function. This half is fifty times larger, so it used the tool that failure
argues for: an AST walk collecting every `ast.Name` node's `(lineno, col_offset)`, rewritten
bottom-up. Comments and strings are untouched by construction.

Two things that had to be right, and one that was not at first:

1. **Scope.** A durable name is rewritten inside a closure only when it is FREE there. A name
   assigned in a closure without `nonlocal` is that closure's own local; rewriting its loads
   would change which variable is read. Measured: no closure shadows any durable name, so the
   rule fired zero times -- but a scan that had not checked would have been luck.
2. **`col_offset` is a UTF-8 BYTE offset, not a character index.** This file is full of
   box-drawing and em-dash characters, so the first attempt spliced a few characters off and
   produced `dit.p` out of `audit`. It asserted rather than writing, which is the only reason
   that is a footnote. The splice now happens on bytes.
3. **A method's indent.** `_shift_close_out` already sat at one level inside `_build_leaf` --
   exactly a method's indent -- and adding four more made it a nested `def` inside `__init__`.
   It parsed, it ran, and the class simply did not have the method. Caught by asserting
   `hasattr` rather than by the tests, which would not have noticed until a day boundary.

### Two names that were arm state by accident

`_d` and `_q` are loop variables -- `for _q in mgr.put_queues`, `_d = _xp * _b.x_phys + ...` --
bound at the top level as construction temporaries and then declared `nonlocal` in two
closures, which is what drags a throwaway up to arm scope. Every use is a fresh bind a few
lines from its read, in one function, so the declaration went and they are closure locals
again. They are not fields.

### Ten tests moved out from under this, and every one was re-expressed

All ten were source-shape scans over `_build_leaf` -- `inspect.getsource` plus a substring or
an AST walk -- asking real questions about the arm: where the crew comes from, where the day
origin comes from, how the uid cursor advances. The construction they read is now in
`_build_arm`, so they read `_leaf_source()`: `_build_arm` + `_build_leaf` +
`ArmAssembly.shift_close_out`, the method dedented so the result still parses.

One assertion was pinned to the COLUMN a continuation line started in, which moved when the
close-out became a method. It is whitespace-normalised now -- the claim was always about the
call and its arguments.

### Verification

| check | result |
|---|---|
| toy run vs baseline, `run_digest.py` | **IDENTICAL**, 136 arms |
| `Tests/unit` + `Tests/integration -k "not gpu"` | 3,161 passed / 2 skipped |
| gates | 1-5, 7-10 green; 6 red as at the phase-0 baseline |

### Still true, and left alone

`_build_leaf` is 918 lines, not the ~30 the ticket estimated, because `_replenish`, `_step`
and `_finish` ARE the stepping half -- the ticket asked to split where construction stops and
stepping starts, and that is where it is split. Moving `_step`'s 594 lines onto the object as
well is a different question (it would make `ArmAssembly` a god object rather than a record)
and is not this ticket's.
