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
