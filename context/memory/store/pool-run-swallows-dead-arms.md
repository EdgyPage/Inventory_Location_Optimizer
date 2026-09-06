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
