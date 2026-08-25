---
name: config-knob-has-five-seams
description: "a new config knob needs FIVE wirings, not the four settings.py names — the fifth is workunits._shared, and skipping it means the knob silently reverts to its default inside every spawned worker"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-25T13:13:54.925Z
---

`Optimization/config/settings.py` documents the wiring for a new knob and names four seams.
There is a **fifth**, and omitting it fails with no error: the knob works in-process, works in
tests, works in a single-worker run, and quietly reverts to its default in every real run.

The full contract:

1. declared in `Optimization/config/settings.py`;
2. threaded into `CONFIG` (`sim_config.py`, usually via an accessor);
3. exposed as a CLI flag;
4. recorded in the run spec, and restored in **both** `_apply_run_spec` and
   `run_analysis._apply_run_shape` — two restore sites, not one;
5. **carried in `workunits._shared`, the picklable worker payload.**

Why 5 is invisible: the pool is **spawn**, not fork (CLAUDE.md §2). A worker re-imports
`sim_config` from scratch and gets pristine module defaults — whatever the parent's CLI flag
set is simply not there. Only what rides `_shared` crosses the process boundary. So the
symptom is a run whose spec records `cut_at_day_end=True` and whose every worker cut nothing.

**Cheapest check:** if the knob's effect shows up under `-m pytest` but not in a real sweep,
look at `_shared` before looking anywhere else.

See [[heredoc-python-breaks-the-spawn-pool]] (the same spawn/re-import mechanism, different
symptom), [[one-clock-one-speed-one-config]], [[worker-recycling-pinned-at-one]].
