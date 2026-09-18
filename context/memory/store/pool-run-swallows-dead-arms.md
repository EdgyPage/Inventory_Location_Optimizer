---
name: pool-run-swallows-dead-arms
description: "a run_simulation pool run whose EVERY worker dies still prints 'All simulations complete' and exits 0; the tell is 'These arms produced no data' plus 'Config stage: 0 job(s)' in run.log, never the exit code"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9a479cc3-4b40-44e6-b896-c76f39c2a58d
  modified: 2026-09-06T02:52:50.680Z
---

`python -m Optimization.run_simulation` finished with exit code 0 on 2026-09-05 while all 68
arms had died in their spawned workers on a `KeyError` (a payload key indexed in the wrong
shape). The harness logged each traceback as a `_RemoteTraceback`, listed every DB under
"These arms produced no data (likely a placement/stocking failure)", ran the analysis stage
over nothing (`Config stage: 0 job(s)`), wrote a dossier, and reported success.

**Why:** the flat pool treats a worker failure as a per-arm retry-then-skip outcome, not a run
failure, so the run root exists, `run_spec.json` is written, and every downstream stage tolerates
an empty leaf set. Nothing raises at the top.

**How to apply:** after ANY pool run, before trusting it, check three things in `<run_root>/run.log`:
`grep -c Traceback` is 0, "produced no data" is absent, and the analysis `Config stage:` line has a
non-zero job count on a run that simulated. A green exit is not evidence; the same silence as
[[coverage-e2e-swallows-worker-logs]] and [[a-grant-is-not-an-output]]. Verify a seam that reaches
the worker (see [[config-knob-has-five-seams]]) by reading the DB (`simulation_runs`,
`picker_events`), never the log tail.

**FIXED 2026-09-18 -- exit 1, no analysis, and the resume command on the last lines.**
The detection was never missing: `supervisor._supervise` had logged the unrecovered units at
ERROR with a resume command since the retry driver was written. What was missing was three
return values -- `_supervise` -> `_run_workers_flat` -> `_run_scenario` -> `_run_whatif_matrix`
each returned None -- and a decision at the top. Now `_supervise` returns the sorted unrecovered
unit ids, the matrix reports them as `info['unfinished'] = {cell: [ids]}` (a clean matrix is an
empty dict), and `run_simulation._refuse_incomplete` raises `SystemExit(1)` BEFORE the analysis
stage, so "All simulations complete." cannot print over holes and a detached driver can read the
outcome from the status. Pinned hop by hop in `Tests/integration/test_crash_recovery.py`.

**How to apply (amended):** the three greps above still hold for any run recorded before
2026-09-18 and for a run that finished with every arm alive but blank (a blank DB is a
`_warn_blank_arms` WARNING, not an unrecovered unit -- that class is still exit 0). For a run
after that date, a non-zero exit IS the signal; a zero exit still does not prove the data, it
proves the pool recovered every unit.
