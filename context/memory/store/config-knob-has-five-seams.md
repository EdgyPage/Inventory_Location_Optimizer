---
name: config-knob-has-five-seams
description: "a new config knob needs FIVE wirings, not the four settings.py names — the fifth is workunits._shared, and skipping it means the knob silently reverts to its default inside every spawned worker"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-09-10T21:17:42.087Z
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

**Update 2026-09-08 (ADR-0004):** a staffing key can now be regime-conditional, not just a
plain default -- `Optimization/config/sim_config.py:ERA_ONLY_KEYS` /
`Optimization/config/sim_config.py:FLAG_OFF_ONLY_KEYS`, read by
`Optimization/config/sim_config.py:staffing_spec()`, which records the OTHER regime's keys as
`None` (e.g. `store_pickers` is `None` under the era, `store_demand` is `None` flag-off). A key
absent from both lists still needs all five seams above; a staffing key present in either list
is `None` by design in the off regime -- do not "fix" that None as a missing wiring.

**Update 2026-09-10 (ticket 35):** `min_headroom` is a fresh era-only example, not just the
2026-09-08 three -- declared `Optimization/config/settings.py:MIN_HEADROOM` (0.05), threaded
via `Optimization/config/sim_config.py:min_headroom()`, exposed as `--min-headroom` and
refused flag-off, and carried in `ERA_ONLY_KEYS` alongside `store_demand`/`ff_demand`/
`first_time_confidence`. See [[derived-fill-is-the-fourth-comparability-break]].

**Update 2026-09-10 (inbound 23): a GUARD on a declared key is era-blind.** Derived values are
never CONFIG keys, so under the era a declared staffing key stays at its flag-off default
(`recv_crew_size` records 0) while the crew lives in `derived.receiving.crew`. Any accessor
that tests the declared key -- `sim_config.inbound_spec()`'s standing-yard guard did, and so
did the resume planner's `receiving=` one call earlier -- refuses or misjudges every era run.
The pattern is `recv_crew_spec(size=)`: the caller that holds the derived block hands the
value in, None reads the declared key. Grep for `g.get('recv_crew_size')` /
`g.get('put_crew_size')` before adding a check that must hold under both regimes.

See [[heredoc-python-breaks-the-spawn-pool]] (the same spawn/re-import mechanism, different
symptom), [[one-clock-one-speed-one-config]], [[worker-recycling-pinned-at-one]],
[[nothing-is-lost-under-the-era]].
