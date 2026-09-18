---
name: detached-runs-import-the-working-tree
description: "A detached run spawns a FRESH process per job (max_tasks_per_child=1) that imports the WORKING TREE at that instant, so editing the tree during a run reaches every later worker; on 2026-09-18 a half-applied edit killed all 12 campaign workers at import and hung the driver. Run campaigns from a git-archive copy of HEAD, never from the tree you are editing."
metadata:
  type: feedback
---

**What happened (2026-09-18 09:31).** The phase-2 campaign was launched at 09:06 as a detached
scheduled task from the working tree. At 09:30 a patch adding a book to `AisleLedger.POLICY_BOOKS`
was applied to that tree without its evaluator view; `strategy_runner`'s import guard refuses that
tree with a RuntimeError. At 09:31:19 the campaign's first pool spawned 12 workers -- spawn, not
fork, so each child re-imports every module FROM DISK -- and all 12 died at import in 3 seconds.
`run.log` shows only `[supervisor] worker pool BROKEN (hard worker death)`; the children's stderr
went nowhere (no console), no Application-log crash record exists (a Python exception at import
is a clean exit), and the driver then hung at zero CPU (see the ticket below). The edit was fixed
at 09:37; by then the run was dead. Nothing in the run had any way to say why.

**Why:** the parent imported the tree once at 09:06 and kept running fine, which is what made
the tree feel frozen. It is not: `max_tasks_per_child=1` (pinned, see
[[worker-recycling-pinned-at-one]]) means EVERY unit is a fresh spawn and a fresh import, so the
code a campaign runs is whatever is on disk when each of its 120 units starts. That is also a
comparability hazard even when nothing breaks: arms in different cells silently run different
code if a commit lands mid-campaign.

**How to apply:** launch any run longer than a few minutes from an IMMUTABLE copy of HEAD --
`git archive HEAD | tar -x -C <dir>` plus a copy of `.env` (untracked; without it
`COMPARISON_OUTPUT_DIR` falls back into the source tree) -- and set the scheduled task's
working directory to that copy ([[head-copy-via-git-archive]] for why not `git worktree`). A
resume needs no `.git`: `run_spec.json` keeps the original commit on record. Record the copy's
commit in the effort map. Never edit the tree a detached run is importing from; if you must
change code while a run is up, change it in the repo and leave the copy alone. Related:
[[launch-long-drivers-detached]] (the no-console recipe this extends),
[[pool-run-swallows-dead-arms]] (the exit status this defeats when the driver hangs),
[[heredoc-python-breaks-the-spawn-pool]] (the other spawn-time import trap).
