---
name: coverage-e2e-swallows-worker-logs
description: "Tests/bench/coverage_e2e.py sinks worker logs into a queue nobody drains, so every ledger and error a pool worker logs is invisible through the byte-identity harness"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T01:45:11.874Z
---

`Tests/bench/coverage_e2e.py:59` hands pool workers a `QueueHandler` whose queue is never
drained. Everything a worker logs — including `strategy_runner`'s conservation ledger and the
demand ledger, both of which are `log.error` — goes into it and is never printed.

**Why:** the harness that proves byte-identity is also the one used to smoke-test behaviour
changes, and a run that is silently violating an invariant looks exactly like a clean one.
A deliberately re-introduced over-pick produced zero visible output through this path.

**How to apply:** never conclude "the ledgers were silent" from a `coverage_e2e` run. To read
them, run the same workload with a listener that drains the queue (the scratchpad script
`drain_worker_logs.py` does this), or run the strategy inline. Fingerprint comparisons
themselves are unaffected — they read the DBs, not the logs — and must exclude
`simulation_runs` and `warehouse_stats`, whose timestamps are nondeterministic.
Related: [[pickers-over-picked-until-planned]], [[heredoc-python-breaks-the-spawn-pool]].
