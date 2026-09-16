# 02 - the `_run_strategy_worker` hot-path sentinel never executes under the e2e driver

Type: bug
Status: needs-triage

`Tests/architecture/test_architecture_coverage.py::test_hotpaths_execute_under_e2e_driver`

```
AssertionError: sentinel hotpath never executed:
  {'name': '_run_strategy_worker', 'file': 'Optimization/simdriver/strategy_runner.py',
   'kind': 'function'}
```

The declared hot path is not observed running under the e2e driver.

**Two readings, needing different fixes.** Either the driver no longer reaches it (a real coverage
gap), or it runs and the instrument cannot see it. The second is not hypothetical here: the
`coverage_e2e` harness queues worker logs and never drains them, and the call-tree tracer is
structurally blind to comprehensions and C leaves. `_run_strategy_worker` is also the entry point a
SPAWNED worker runs, and coverage measured in the parent does not see a spawned child's frames
unless it is configured to.

Start by asking whether the sentinel could be executing in a subprocess this measurement never
watches.
