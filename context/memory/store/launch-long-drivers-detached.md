---
name: launch-long-drivers-detached
description: "A long driver must run with NO CONSOLE -- a scheduled task whose action is python.exe directly. Bash background mode dies at the 10-minute cap, and every console-attached launch (Start-Process, WMI, a cmd-wrapped task) takes a ^C when the launching tool call returns"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: aa6f89e7-91b2-49c5-a80d-485be5208823
  modified: 2026-09-06T18:20:54.255Z
---

**CORRECTED 2026-09-12: the Start-Process recipe below no longer survives.** Taking dept-cal
46's run, THREE launches died within ~60 s, each with a literal `^C` as the last bytes of its
log and the wrapper's `echo EXITCODE` line never reached -- so `cmd.exe` was handed a console
control event; it was not a crash (stderr empty, no traceback, no partial output). All three
died at the moment the launching tool call returned: `Start-Process
-RedirectStandardOutput`, a WMI `Win32_Process.Create` of a cmd wrapper, and a scheduled task
whose action was a cmd wrapper.

**And the console is only half of it: MODERN STANDBY will kill a long run outright.** The
surviving launch above still died at its analysis stage with `STATUS_IN_PAGE_ERROR`
(0xC0000006, task `LastTaskResult` 3221225478) after Kernel-Power **506/507** transitions --
Application Error 1005, "Windows cannot access the file ... the disk that the file is stored
on". **Do not diagnose sleep by Kernel-Power 42/107**: this machine never logs those, and
checking only for them produced a confident and WRONG all-clear while standby was already
running. Query 506/507 (and 566/172) instead.

Before a long run, hold the machine awake (`mcp__ccd_host__request_keep_awake` with
`until: "session_idle"`) and tell the user it blocks IDLE sleep only -- a closed lid or a
manual Sleep still suspends and still kills the run. The damage is bounded: the simulation
work survived intact (all 8 arm DBs `PRAGMA quick_check` ok) and `--resume` recovered it in
five minutes, re-declaring from the run's own record rather than re-deriving
([[resume-architecture-verified-sound]], [[verify-tree-uses-the-runs-own-contract]]).

**What works: a scheduled task whose action is `python.exe` DIRECTLY** -- no `cmd /c`, no
stdout redirection, so the process has no console and there is nothing to interrupt.
`run_simulation` writes its own `run.log` into the run root, so redirection buys nothing
anyway; watch THAT file.

    $a = New-ScheduledTaskAction -Execute <python.exe> -Argument '-X utf8 -u <script> ...' \
         -WorkingDirectory <repo>
    $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::FromHours(4)) \
         -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName <name> -Action $a -Settings $s -Force -RunLevel Limited
    Start-ScheduledTask -TaskName <name>

Then a Monitor that tails `<run_root>/run.log` and exits when
`(Get-ScheduledTask -TaskName <name>).State` stops being `Running`. Unregister the task when
the run is done. A `^C` at the end of a driver log means THIS, not a user interrupt and not a
crash -- do not go looking for a traceback.

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
