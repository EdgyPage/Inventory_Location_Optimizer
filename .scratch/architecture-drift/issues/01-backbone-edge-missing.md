# 01 - the `build_shared_assets` -> `plan_warehouse` backbone edge is absent

Type: bug
Status: needs-triage

`Tests/architecture/test_archgraph_extract.py::test_key_backbone_edges_present`

```
AssertionError: assert False
 +  where False = has('sim_assets.py::build_shared_assets',
                      'inventory_planning.py::PlanningMixin.plan_warehouse', 'calls')
```

A declared backbone edge is not in the extracted graph. Either the call moved behind an
indirection the extractor cannot follow, or the edge is genuinely gone and the backbone list is
stale.

**Check this one first.** The evidence above comes from a run that PREDATES the derived-layer
resync in `45e7728f`, which regenerated `graph.json` from source. A backbone edge is read off that
graph, so this may already be resolved. `python context/arch/verify_architecture.py` passed after
the resync, but it verifies boundaries and freshness, not this test's backbone list.

## Diagnosis from the architecture-deepening effort (2026-09-16)

Not claiming this ticket -- handing over what I found while checking whether a refactor of
`PlanningMixin` would collide with it.

**The edge is NOT missing. Its SOURCE is a nested function.** `context/arch/graph.json` holds:

    src: Optimization/simdriver/sim_assets.py::build_shared_assets._plan
    dst: Warehouse/inventory/inventory_planning.py::PlanningMixin.plan_warehouse
    kind: calls

`Tests/architecture/test_archgraph_extract.py::test_key_backbone_edges_present` asserts the
source is `build_shared_assets`, but the call lives in the closure `_plan` defined inside it
(`sim_assets.py:131`), and the extractor attributes it to the closure -- correctly, by its own
rules.

So this is not a resolution failure and not a stale capture: it is a question about what a
declared BACKBONE edge should mean when the call sits in a nested function of the named
source. Either the assertion names the closure, or the verifier rolls nested calls up to their
enclosing top-level function. That is a semantics decision for this effort's owner, not
something to patch from outside it.

Two things worth knowing with it:

- **Gate 2 is GREEN while this test FAILS.** `context/arch/verify_architecture.py` passes its
  SCOPE check on `architecture.yml`'s `backbone` list, so the two carry different edge sets.
  A fix should probably reconcile them rather than only silence the test.
- The first hypothesis I tried was wrong and is recorded so nobody repeats it: `sim_assets`
  calls `Inventory_Manager.plan_warehouse` (the manager, not the mixin) and the method is a
  `@classmethod`, so "the MRO pass does not resolve a classmethod accessed on the class"
  looked likely. It is not the cause -- the dst resolved fine.
