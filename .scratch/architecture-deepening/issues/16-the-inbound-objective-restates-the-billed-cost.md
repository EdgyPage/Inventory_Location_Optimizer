# 16 - the inbound objective restates the cost model the simulation bills

Type: refactor
Status: resolved
Blocked by: 02

## Context

The objective the inbound gain arms optimize is a **second implementation** of the cost model the
simulation charges. It shares the kernel primitives (`per_pick`, `height_multiplier`, imported at
`Inbound/gain.py:129`) but restates the formula, and nothing joins the two.

What the simulation bills, `Warehouse/operations/putaway.py:98-119` (`put_cost`, called from
`Inventory_Management.py:1342`):

```
travel + M(y) * (intercept + qty*per_item + qty*var)
```

What the arms optimize, `Inbound/gain.py:836-878` (`_cost_at`, the put term at `:869`):

```
put = ps.x_pace * x + ps.y_pace * y
```

No intercept, no per-item charge, and **no height multiplier** -- which is bin-dependent and
therefore does not cancel out of a gain difference between two candidate placements.

**This is not a bug claim.** `gain.py:77` records the simplification as a decision ("put travel is
paid once at the put crew's speeds"). The claim is that **whether it is a decision or drift is
undecidable from the tests.** All 57 `def test_` in `Tests/unit/test_gain_plan.py` validate the
evaluator against a naive rebuild of itself (`:706`) or a closed form of its own formula (`:746`).
Not one references `put_cost`, `Pick._pick_time`, or `Warehouse.operations.putaway`.

The formula has moved three times in two months -- ADR-0001 (the per-item charge, "a HARD BREAK for
every pick/put/labor number"), the 2026-08-24 one-clock refactor, ADR-0003 (put-away fills empty
bins first). Each changed what a put costs. Nothing would have told you whether the gain arms moved
with it.

## What to build

**(a) Give the objective a seam onto the billed cost.** Extract from `Warehouse/kernel/cost_model.py`
(or `putaway.py`) a pure `put_seconds_at(x, y, unit, wp, speed, cost)` -- and its pick counterpart
-- that BOTH `Inventory_Manager._place` and `_Evaluator._cost_at` call. The evaluator keeps
everything it owns (the visit expectation, the window-rate substitution, the moments, the
exhaustion penalty) and stops owning the at-bin expression. **Where the objective deliberately
drops a term, it drops it visibly, as a named argument, not by not writing it.**
`Inbound -> wh_kernel` is already a legal edge (`gain.py:129-131`).

**(b) Split the file, separately.** `Inbound/gain.py` is 1,398 lines and is four modules:
`:227-398` the copy-on-write views (~170 lines, two dedicated equivalence test files), `:400-730`
the owner resolution (~330), `:731-1236` `_Evaluator` + `plan_order` (~500), `:1303-1398` the
entries. Deletion test on the first two: deleting the CoW module brings back a measured 37.8x copy
blow-up at every `_make_pool`; deleting the bundle trio brings `if coupled:` back into the pricing
path, which `gain.py:57-60` says explicitly it exists to prevent. Both earn their keep -- they are
just not the same module as the evaluator.

**The template is in this repo already.** `Inbound/unload.py:60-71` (`UnloadCost.from_putaway`)
makes exactly this argument: a reference makes the drift "structurally impossible rather than merely
a diff someone might notice". The gain evaluator is the one costing path that did not get it.

## Verification

- The missing test becomes writable: hand the same unit and bin to `put_cost` and to the
  evaluator's put term, assert the documented relationship -- equal, or equal minus the named
  dropped term. Today no test can fail when the two diverge.
- Byte-identity: if the unification changes any number, that is a finding, not a refactor. Prove
  DB-row neutrality with `run_digest.py` or stop and record what moved.
- Gates 1, 2, 10.


---

## RESOLVED 2026-09-17 — (a) landed here, (b) split to ticket 22

### What landed

`putaway.put_seconds_at(x, y, *, speed, cost=None, weight, volume, quantity)` is now the ONE
at-bin put expression. `put_cost` keeps its name — it is the BILLING call and reads as one at
`_cost_putaway` — but has no arithmetic of its own; it is `put_seconds_at` with the cost in.
`_Evaluator._cost_at` calls the same function and **drops the handling term by passing
`cost=None`**, which is the whole point: the simplification is refused at the call site, by
name, where a reader sees it and a test can pin it. It used to be a term that simply was not
written.

`Tests/unit/test_put_seconds_at.py` is the test the ticket said was unwritable. Six of them,
and two matter:

- `test_cost_none_drops_exactly_the_handling_term` states the relationship as an EQUATION, not
  as "the optimised reading is smaller", so a change to either half lands here.
- `test_the_dropped_term_is_bin_dependent_which_is_why_it_matters` asserts the concrete
  reason: `M(y)` is a step function of height, so two bins in one aisle at the same x differ
  in billed handling while the objective scores them identically. A dropped CONSTANT would
  cancel out of a comparison between bins; this one does not.

The two source-checked tests are deliberate: the failure this ticket is about is not a wrong
number, it is a second implementation that agrees today. A value test cannot tell those apart,
and `test_put_cost_has_no_arithmetic_of_its_own` is what says there is one body. Proved
non-vacuous by restoring the old inline form — the objective test fails and names it.

### Byte-identical, verified

Toy-run digest IDENTICAL over 136 arms (batched with ticket 17). Expected by construction —
the objective passes `cost=None` and gets exactly the travel expression it wrote before — but
the ticket asked for proof rather than the argument.

### (b) is now ticket 22, and it carries a trap

Splitting the 1,398-line file is real work with a real hazard, and folding it into this
ticket would have hidden the hazard behind a resolved status. Ticket 22 holds it. The trap,
recorded here because it is what made the split worth its own file:

`Tests/unit/test_gain_cow_equivalence.py` REBINDS `gain.AISLE_VIEWS` to sabotage the views
(`:51`, `:140`), and its own docstring calls that the saving throw — "without this the file
passes if AISLE_VIEWS is pointed back at AISLE_COPIERS". If the views move to their own module
and `_make_pool` reads them through `from ... import AISLE_VIEWS`, rebinding `gain.AISLE_VIEWS`
stops reaching the reader and **the sabotage silently stops sabotaging**. That is not
hypothetical: `test_gain_bundle_labor_families.py:240` records the same thing happening once
already, when `_make_pool` moved from `AISLE_COPIERS` to `AISLE_VIEWS`.

So the split is safe only if the evaluator reads the table through its MODULE
(`_cow.AISLE_VIEWS[n]`, not a bare imported name) and the two tests are re-pointed and
re-proved against planted damage. Mechanical, but it is the kind of mechanical that has
already cost this repo a dead test.
