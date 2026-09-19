# 01 - a pool whose every worker dies at import hangs the driver instead of retrying or exiting

Type: task
Status: resolved

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

## Answer -- RESOLVED (2026-09-18)

**Reproduced, with the thread dump, and the ticket's diagnosis was half right.** The `with`
exit IS the wait, but what it waits on is not the Manager: `shutdown(wait=True)` joins the
executor's manager thread; that thread, in `terminate_broken -> join_executor_internals ->
call_queue.join_thread()`, joins the call queue's FEEDER thread; and the feeder is inside
`PipeConnection._send_bytes`, on an overlapped WriteFile no live worker will read. A Windows
pipe buffer is 8 KiB. **One 16 KiB unit argument with TWO workers hangs it**; with small
arguments every write fits the buffer and nothing hangs -- which is why the first fixture
(2 workers, 2 units, ~300-byte payloads) returned in 0.3 s and looked like a refutation, and
why `test_crash_recovery.py`, which fakes `_run_pool`, could never have seen it. The real
payload is the CONFIG snapshot plus the shared paths, times twelve queued units.

This is CPython gh-107219, fixed in 3.11.5 / 3.12 (`Queue._terminate_broken` closes the
connections). The machine runs **3.11.4**. The stdlib's own fix was measured NOT to work here
(`pipe_unblock_probe`): closing the writer from another thread leaves the write pending;
closing the reader ends the pipe in isolation (`BrokenPipeError` at once) but not under the
pool -- a child that dies at bootstrap never steals the handle duplicates `reduction.DupHandle`
made for it in the parent, so the parent still holds a live reader (the likely mechanism;
the drain below does not depend on it). What always works: **read the parent's own end**.

**Built:**

1. `supervisor._unblock_broken_pool` -- on the BrokenProcessPool path, before the `break`,
   drain `pool._call_queue._reader` until the feeder thread exits (bounded, 60 s). Each
   `recv_bytes` completes one blocked write; the manager thread's `close()` appends the
   sentinel; the feeder exits; the `with` exit returns; `_supervise` retries. Measured: the
   16 KiB / 2-worker and 64 KiB / 12-worker shapes now retry twice and return in < 1 s.
2. `supervisor._explain_worker_death` -- once per `_supervise`, on the first break: a fresh
   interpreter (no multiprocessing) re-runs the main module as `__mp_main__` exactly as
   spawn does, imports the worker's module, and its CAPTURED stderr tail goes into the
   parent's log. The 37 silent minutes become a line naming the RuntimeError.
3. `Tests/integration/test_supervisor_broken_pool.py` + `_broken_pool_driver.py` +
   `_worker_dies_at_import.py` -- a REAL spawn pool, both death points (during bootstrap and
   at the call item), the two hanging shapes, a small-argument control, under two watchdogs
   (the driver's own `os._exit(3)` with a full thread dump; the test's tree kill). A hang is
   a failed assertion, never a hung suite. In the eleventh gate.

**Not built:** the "bounded join on the Manager" -- the Manager was never the wait. Noted for
the fixture: a Manager whose server child dies at bootstrap hangs `Manager()` itself in
`start()`, forever, so the fixture creates it before "breaking" the tree, as the launch did.
