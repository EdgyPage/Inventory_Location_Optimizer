# 22 - Inbound/gain.py is 1,398 lines and four modules

Type: refactor
Status: open
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
