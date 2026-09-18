# 01 - a pool whose every worker dies at import hangs the driver instead of retrying or exiting

Type: task
Status: needs-triage

**Seen 2026-09-18 09:31:22** on the phase-2 launch (`comparison_whatif_20260918_090637`). The
first cell's pool opened 12 workers; all 12 died within 3 seconds -- at IMPORT, because the working
tree the spawned children re-imported carried a half-applied edit that `strategy_runner`'s
`AISLE_VIEWS`/`POLICY_BOOKS` guard refuses (see memory `detached-runs-import-the-working-tree`).
`run.log`'s last line is

    [supervisor] worker pool BROKEN (hard worker death) - abandoning this pool; unfinished units will be rebuilt + resubmitted

and then NOTHING for 37+ minutes: no `[supervisor] retry 1/2` line, no second `Flat pool` line, no
exit. The driver (pid 15620) and its multiprocessing Manager (pid 20448) were both alive at
**0.00 s CPU over 6 s, ~50 threads each, ~130 MiB** -- hung, not rebuilding. Both had to be killed
by hand.

## Where it hangs

`supervisor._run_pool` catches `BrokenProcessPool` inside `as_completed`, logs the line above and
`break`s -- while still INSIDE `with ProcessPoolExecutor(...) as pool:`. The block's exit is
`pool.shutdown(wait=True)`. With every child dead before it ever registered, and the log queue a
Manager `Queue` the children held handles to, that shutdown never returns. `_supervise`'s retry
loop (rebuild + resubmit, `max_retries=2`) is never reached, so neither is the exit-status path
`0c213e91` added: a run in this state neither retries nor exits 1 -- it holds the machine.

## What to build

1. **A test that reproduces it under a hard watchdog** (the shape W6 asked for on the recycling
   pin): a worker entry whose module raises at import, a `ProcessPoolExecutor(max_tasks_per_child=1)`
   over it, `_run_pool` around it, and a watchdog that FAILS the test on a hang rather than hanging.
2. **Then the fix**, most likely `pool.shutdown(wait=False, cancel_futures=True)` on the broken
   path (the `with` exit is what waits), and a bounded join on the Manager. Retry then runs; if
   the tree is still broken the retries exhaust and `_refuse_incomplete` exits 1 with the resume
   command -- the outcome `pool-run-swallows-dead-arms` and `0c213e91` intended.
3. **Say WHY the workers died.** A child that dies at import writes its traceback to a stderr
   nobody has under a scheduled task. The parent knows only "hard death". A cheap probe -- spawn
   ONE child that imports the worker module and reports -- before opening a 12-wide pool would
   have turned 37 silent minutes into one log line naming the RuntimeError.

Not fixed on 2026-09-18: the campaign was restarted from an immutable copy of HEAD instead
(map, Decisions). This ticket is the driver-side defect that turned an edit into a hang.
