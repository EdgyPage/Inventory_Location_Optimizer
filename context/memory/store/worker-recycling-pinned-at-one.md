---
name: worker-recycling-pinned-at-one
description: "Pool worker recycling is pinned at 1 by decision, not tuning — a queue of tasks per worker deadlocked the sim at a cell boundary"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5eb3d052-8ad3-4cf3-b0d4-c2ec1fa4ece2
  modified: 2026-09-19T23:55:54.188Z
---

`ProcessPoolExecutor`'s `max_tasks_per_child` is **pinned at 1** in `simdriver/supervisor.py`.
It is not a knob to tune. `--max-tasks-per-child` is still accepted (old saved run specs pass it)
but warns and is ignored.

**Why:** the flag had never actually reached the pool — every run in this repo's history recycled
at 1 without anyone knowing. The first run that honoured a larger value (6) finished cell 1 of a
2-cell sweep, then sat at **zero CPU with one live worker of eighteen** and never shut down. No
error, no traceback. History supplied a clean A/B: same profile, same machine, recycle=6 hung
after one cell, recycle=1 completed both.

The user's standing principle, stated when choosing the pin over debugging the hang: *"crowding
the workers with a queue of tasks is a recipe for bugs in long running code."* The rejected
alternative was plumbing the flag through properly and picking a safe value — rejected because
the saving is ~10 s of process spawn against jobs measured in minutes, which buys nothing against
a multi-hour run that can hang silently.

**How to apply:** do not re-plumb this flag, and do not propose per-worker task batching as a
performance idea here. If pool throughput needs work, look at job granularity instead — see
[[analyze-run-granularity-worker-saturation]].

**UPDATED 2026-09-19 — the pin moved, and it is no longer universal.** Since the flat work pool
([[flat-work-pool-era]]) the pin lives in `Optimization/simdriver/supervisor.py:_sim_executor`,
the ONE place the sim-side executor is built (`WorkPool` itself requires an executor factory and
builds none of its own). The analysis-side executor,
`Optimization/run_analysis.py:_analysis_executor`, is DELIBERATELY left unpinned: its per-worker
context caches are evicted per cell inside the worker instead of relying on process recycling to
clear them, and analysis jobs are seconds long, not minutes. `Tests/unit/test_worker_recycling_pin.py`
pins both — the sim executor at 1, the analysis executor unpinned.
