# 22 - Inbound/gain.py is 1,398 lines and four modules

Type: refactor
Status: resolved
Blocked by: -

Split out of ticket 16, whose own text calls it "(b) Split the file, separately". 16(a) -- the
objective's seam onto the billed cost -- landed 2026-09-17; this is the rest.

## What it is

| lines | what | deletion test |
|---|---|---|
| ~227-398 | the copy-on-write views | deleting them brings back a MEASURED 37.8x copy blow-up at every `_make_pool` |
| ~400-730 | owner resolution (`GainBundle`, `OneOwnerBundle`, `SiteGainBundle`) | deleting the trio brings `if coupled:` back into the pricing path, which `gain.py:57-60` says it exists to prevent |
| ~731-1236 | `_Evaluator` + `plan_order` | the evaluator itself |
| ~1303-1398 | the entries | |

Both of the first two earn their keep. They are just not the same module as the evaluator.

## THE TRAP -- read this before moving anything

`Tests/unit/test_gain_cow_equivalence.py` REBINDS the module global to sabotage the views:

```python
gain.AISLE_VIEWS = views                                   # :51
gain.AISLE_VIEWS = dict(saved_views, aisle_sku_sets=counted)  # :140
```

and its own docstring calls that the saving throw -- *"without this the file passes if
AISLE_VIEWS is pointed back at AISLE_COPIERS"*.

If the views move to their own module and `_make_pool` reads them through
`from Inbound.gain_cow import AISLE_VIEWS`, the name in the evaluator's module is bound at
import and **rebinding `gain.AISLE_VIEWS` stops reaching the reader**. The sabotage silently
stops sabotaging and the file passes for the wrong reason.

**This is not hypothetical.** `test_gain_bundle_labor_families.py:240` records it happening
once already: that file used to patch `AISLE_COPIERS`, and when `_make_pool` moved to the
copy-on-write `AISLE_VIEWS` the sabotage quietly stopped biting.

So the split is safe only if:

1. the evaluator reads the table through its MODULE -- `_cow.AISLE_VIEWS[n]`, never a bare
   imported name;
2. both sabotage sites are re-pointed at that module;
3. each is re-proved against planted damage, not merely re-run.

## Also in scope

`strategy_runner` imports five names from `Inbound.gain` and checks `AISLE_VIEWS` against
`AisleLedger.POLICY_BOOKS` at import (ticket 02 stage B). Those imports move with the table.

## Verification

- Gates 1, 2 (a new module is new import edges), 3, 10.
- `Tests/unit/test_gain_cow_equivalence.py` and `test_gain_cow_protocol.py` green AND proved
  to still fail on planted damage -- the whole point of this ticket's trap.
- No digest needed if nothing but imports moves; say so explicitly if a body moves with it.


---

## RESOLVED 2026-09-17

`gain.py` 1,410 lines -> 865, plus `gain_cow.py` (251) and `gain_bundle.py` (296). Four things
in one file became three modules; the evaluator and the entries stayed together because
`plan_order` and the four entries are the evaluator's own surface.

### The trap, closed and PROVED

The evaluator reads `_cow.AISLE_VIEWS[n]` through the module, and **`gain.py` does not
re-export either table**. That second half matters as much as the first: a name that still
resolved from `gain` would let the old rebinding look like it worked while reaching nothing.
Every reader was repointed and the two sabotage sites now rebind `gain_cow.AISLE_VIEWS`.

Proved rather than argued. Planting the exact trap -- the evaluator binding the table at
import instead of reading it through the module --
`test_the_view_copies_strictly_less_than_the_copy` FAILS. So the rebinding still reaches the
reader, and the module-read is what keeps it reaching.

A first attempt at that proof was wrong and worth recording: pointing `AISLE_VIEWS` at the
eager copiers at module level changed nothing, because the test REBINDS the table itself and
overwrote the plant. The damage has to be to the CHAIN, not to the table.

### Nothing but imports moved, and that is checked

Twenty top-level definitions exist on both sides of the split, none lost and none new. Their
bodies were compared by `ast.unparse`, which normalises formatting and comments away, and
**exactly one changed**:

    -        state = {n: AISLE_VIEWS[n](d) for n, d in b.aisle_state.items()}
    +        state = {n: _cow.AISLE_VIEWS[n](d) for n, d in b.aisle_state.items()}

So no digest, which is what this ticket said to do if nothing but imports moved -- with the
comparison above as the evidence rather than the claim.

### Two things the move needed

- `from math import isclose`, which `_site_wide_disagreement` used from `gain.py`'s imports.
  Found by walking the new modules' ASTs for names that are loaded and never bound, not by
  waiting for a test.
- The catalogue auto-filled both new files' `purpose` from their docstring first lines instead
  of `TODO` (memory `catalog-merge-seeds-a-docstring-fragment`), so the fill step would never
  have seen them. Checked rather than assumed; both read correctly.

### Verification

`Tests/unit` + `Tests/integration -k "not gpu"` 3,161 passed / 2 skipped. Gates 1-5 and 7-10
green -- gate 5 needed the full `preflight` because the split touched shape-defining source,
and the two canaries reported **tree shape UNCHANGED**. Gate 6 red as at the phase-0 baseline.
