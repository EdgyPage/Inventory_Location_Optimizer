---
name: launch-long-drivers-detached
description: "A simulation driver launched via the Bash tool's background mode dies at the 10-minute cap and orphans run_simulation; launch it detached (PowerShell Start-Process, python -X utf8 -u) and watch its log with Monitor"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: aa6f89e7-91b2-49c5-a80d-485be5208823
  modified: 2026-09-06T18:20:54.255Z
---

Launch multi-pass simulation drivers (`run_reference`, `run_simulation`) as a DETACHED process,
never through the Bash tool's `run_in_background` (600 s cap kills the driver, its
`run_simulation` child keeps running as an orphan, and nothing reports it). Use PowerShell
`Start-Process python -ArgumentList @('-X','utf8','-u','-m',...) -RedirectStandardOutput <log>`
and a `Monitor` that tails the log AND checks the PID with `tasklist`; without `-u` the driver's
redirected stdout is block-buffered and the log lags a whole pass.

**Why:** hit on 2026-09-06 taking the reference run: the first launch died on a cp1252 echo
(fixed in `run_reference.py`), the second would have been cut at 10 minutes mid-pass. Orphaned
`run_simulation` + its preflight canary had to be found with `Get-CimInstance Win32_Process`
and stopped by hand; partial run roots under `COMPARISON_OUTPUT_DIR` had to be removed.

**How to apply:** before launching anything expected to exceed ten minutes, `Start-Process` it,
record the PID, and arm a Monitor whose filter covers failure signatures (`Traceback`,
`produced no data`) and the PID exit, not just the success line. See
[[heredoc-python-breaks-the-spawn-pool]] for the other launch trap and
[[pool-run-swallows-dead-arms]] for why the log must be checked afterwards.
