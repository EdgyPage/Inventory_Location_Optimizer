---
name: config-is-not-a-channel-to-an-evaluation
description: "a run-level value an analysis evaluation needs must be stamped onto sim_result in _sim_result_from_meta — CONFIG cannot carry it, because analysis workers are spawned and re-import pristine defaults"
metadata: 
  node_type: memory
  type: project
  originSessionId: 74b26c9e-b2c5-4720-bf8e-44bf048568e4
  modified: 2026-09-01T04:49:58.198Z
---

The analysis pool is a `ProcessPoolExecutor` and the pool is **spawn**, so an analysis worker
re-imports `sim_config` and gets pristine module defaults. `CONFIG` is therefore **not** a
channel between `run_analysis` (the parent) and an evaluation. The **pickled job** is, and the
per-config half of that job is `sim_result`, assembled by
`run_analysis._sim_result_from_meta`.

That function copies a small, explicit set of keys out of `sim_meta.json`. Anything an
evaluation needs that is not in that set is simply absent — and the failure is silent, because
every consumer of a run-level value has a plausible fallback (this build's default) that
produces a number rather than an error.

**How it actually bit (2026-08-31, ticket 18).** The yard's fee report derives overage at
analysis time from the run's own free-days threshold — that is the whole reason
`yard_trailers` stores raw stamps. `EvalContext.fee_threshold_days` read it from `sim_result`,
`_sim_result_from_meta` had never put it there, and the fallback fired on **every** run.
Recording the threshold in the run spec and restoring it in both restore sites would have
changed nothing on its own: the value reached CONFIG in the parent and stopped there. The fix
is one line in `_sim_result_from_meta`.

**The rule:** a run-level value an evaluation reads needs FOUR things, not three — recorded in
the run spec, restored by `_apply_run_shape` (parent), **stamped onto `sim_result`**, and read
from `ctx.sim_result` (never from CONFIG) inside the evaluation. Grep
`_sim_result_from_meta` before assuming a value is reachable.

This is the analysis-side twin of the simulation-side seam in
[[config-knob-has-five-seams]] — same spawn mechanism, different payload
(`workunits._shared` there, `sim_result` here). See also
[[heredoc-python-breaks-the-spawn-pool]].
