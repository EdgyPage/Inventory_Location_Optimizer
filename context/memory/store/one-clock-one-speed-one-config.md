---
name: one-clock-one-speed-one-config
description: "the 2026-08-24 refactor that gave the sim an absolute clock, an actor model with role x mode, timed put-away and a single config surface — and what it deliberately left provisional"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T19:52:13.164Z
---

Twenty commits (`50d8f33..7478f25`) ahead of the inbound trailer/dock feature.

**The clock.** Every picker's clock was reborn at `0.0` each batch, so `t` alone could not
order two events from different batches. The arm now carries one: every picker starts a
batch at the arm's instant, which advances by that batch's makespan — the running sum, i.e.
`timeline.epochs`. `batch_start_time` / `batch_end_time`, columns that existed and were
pinned to 0, finally carry it.

Uniform across the crew **on purpose**. A per-picker carry (finish early, start the next
wave early) removes the barrier and was implemented first; the fingerprint caught its
failure mode. A picker who draws no task keeps a frozen clock while the others advance, so
`batch_start_time` sticks at 0 forever and `duration` silently becomes an absolute END time
— 6504 s reported against a true makespan of 2262 s. The `start_times` seam on both sims
supports per-picker carry if someone wants it.

**What made it free:** `extract_batch_stats` was reading TIMESTAMPS where it meant SPANS
(`duration`, `task_makespan`, the picking split, `avg_concurrent_pickers`' divisor). Fixing
that first — byte-identical, `Optimization/metrics/` is not in `SHAPE_SOURCES` so it costs no
canary — meant the clock changed **no statistic**: max relative change 0.0000% across 68 sim
DBs, only the two timestamp columns moved.

**New seams:** `cost_model.SpeedProfile` (ft/s → s/inch, one conversion, in `cost_model`
because `forbid: [wh_kernel, "*"]` matches the kernel itself); `Warehouse/operations/`
(`Role`, `Mode`, `Worker`, `Crew`, `put_cost`) importing only `wh_kernel`, so a future
`Warehouse/inbound/` needs no inversion; `work_events` + `work_events_merged` carrying both
streams with a **declared** total order and a SIGNED `qty`; `Inventory_Manager.enable_putaway_timing`
(the `enable_sigma_fd` precedent) making put-away cost seconds — additive, it moves no pick
result; `Optimization/config/settings.py` as the flat authoring surface with `CONFIG` derived
from it.

**Deliberately provisional / left alone:** put-away models no contention and no travel
between placements; lead time stays denominated in **batches** (`Inventory_Manager.LEAD_TIME_UNIT`
— converting moves every restock result and the trailer feature should choose); the
fulfillment `foot` pool is configured to lift at 4 ft/s against the `machine` pool's 2, which
is backwards and looks like a calibration residual — pinned in a test, not fixed.

**Two id spaces, and this is the one that bites.** `actor_local` is dense per crew (what
`picker_events`, `_group_events_by_picker` and `progress_at` require); `actor_uid` is unique
across crews (`work_events` only). A uid in a local slot lands inside `[0, k)` and is
accepted — which is why `_group_events_by_picker` now RAISES instead of silently dropping.

See [[sim-time-unit-is-seconds-not-ms]] (fixed in the same span), [[putaway-seams-for-inbound]]
(the previous refactor's seams), [[case-only-rename-deletes-its-own-page]].
