# Two flow anchors read zero for their whole life, and the gate could not see it

Type: task
Status: resolved

Found while reading a ladder artifact and noticing two flows missing from the output entirely. They
were not missing — they were zero, and the report filters zeros.

## The defect

`yard_plans` and `dock_plans` were declared in `_FLOW_COUNTS` as

```python
'yard_plans': ('gain:plan_order', 'transit:YardTransit.yard_order'),
'dock_plans': ('gain:plan_order', 'transit:YardTransit.dock_order'),
```

the PARENT form, which `_counts_under` resolves over the **direct children** of the parent node.
The real call chain is

```
YardTransit.yard_order -> priorities.bounded_order -> <the arm's registry entry> -> gain.plan_order
```

so `plan_order` is a great-grandchild, never a direct child. Both flows reported **0**.

**Both symbols were in the tree the whole time.** Measured against a real traced inbound scenario:
`gain:plan_order` 4 calls, `transit:YardTransit.yard_order` 4 calls, `transit:YardTransit.dock_order`
4 calls. The names were right, the parents were right, and the RELATIONSHIP was wrong.

These were the two anchors designed to split the yard ranking (unbounded in T) from the dock
ranking (structurally capped at `doors`) — the one measurement that can tell an O(T_yard^2) term
from an O(doors^2) one. They would have reported the inbound entry calls as never having happened.

## Why the existing gate could not catch it

`test_flow_anchors_resolve` walks each anchor's dotted name against its module and asserts the
symbol exists and its `__qualname__` matches. That catches a rename, which is what it was written
for. **It never looks at a tree**, so it cannot see a relationship at all — and its own docstring
says why this class matters more than SECTION_MAP's: *"A broken flow anchor makes the flow report
`0` — and `0` is exactly the reading ('the path never ran') that flows were added to prevent."*
The gate named the failure mode precisely and was structurally unable to detect this instance of it.

## The fix

Two parts, and the second is the one that matters.

**`_counts_anywhere_under`** beside `_counts_under` — a descendant walk, with recursion stopping at
a nested instance of the same parent so two dispatchers cannot double-count one subtree. A SEPARATE
function rather than a parameter on the existing one, because `route_calls` and
`held_retry_touches` are archived series and must not change value. `_FLOW_COUNTS` entries may now
be a 3-tuple `(name, parent, True)` for the deep form; `test_flow_anchors_resolve` resolves the same
two symbols either way and is unaffected.

**`test_every_flow_anchor_that_should_fire_does_fire`** — builds a small inbound cell, traces it,
and asserts that a declared list of ten anchors is non-zero. The list is explicit rather than
blanket, because several anchors are legitimately zero in that configuration (`window_aggs` needs
futuresight, `tier_sorts`/`avail_builds` belong to the merge adapter, `held_appends` needs a staging
floor) and a blanket assertion would have to be weakened until it proved nothing.

Its non-vacuity half asserts `window_aggs == 0` in that same cell: if nothing can be zero there,
the test can no longer distinguish a live anchor from a dead one.

Verified: `dock_plans` 0 -> 2 and `yard_plans` 0 -> 2 against the same scenario;
`Tests/calltree/test_calltree_anchors.py` 11 passed.

## The general shape, which is the reusable part

A symbol table whose entries encode a RELATIONSHIP cannot be verified by resolving SYMBOLS. This
repo has now been bitten by the same shape three times in one effort: a frozen oracle that was
re-priced in production and not in the test tier, a sabotage test patching a table production had
stopped reading, and these two anchors. Each failed silently in the direction of looking healthy.
The check that catches all three is the same: **exercise the thing and assert it produced
something**, not merely that its names still resolve.
