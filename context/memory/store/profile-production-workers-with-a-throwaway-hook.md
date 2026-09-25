---
name: profile-production-workers-with-a-throwaway-hook
description: "To profile real 400k units: a git-archive snapshot whose _run_strategy_worker dumps a cProfile when PERF_PROFILE_DIR is set, plus a 1-cell spec (winner + fifo rider, 3 batches); ~25 min, never in the repo"
metadata:
  node_type: memory
  type: reference
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-25T07:14:27.023Z
---

The driver has no profiling hook, and the in-process calltree harnesses configure their
own regime (coverage, crew), so they don't price production.  The reliable way (S10 of
`.scratch/inbound-fullscale-perf/`, 2026-09-25):

1. `git archive <commit>` into a snapshot directory
   ([[head-copy-via-git-archive]], [[detached-runs-import-the-working-tree]]).  Copy `.env`.
2. In the SNAPSHOT only, rename `strategy_runner._run_strategy_worker` to `..._plain` and
   add a wrapper that, when `PERF_PROFILE_DIR` is set, runs it under `cProfile` and dumps
   `unit_<pid>_<rand>.prof`.  Spawned workers inherit the env var.
3. Add a throwaway spec to the snapshot's `whatif_config`: one inbound cell,
   `rule_pairs=[PHASE2_WINNER, PHASE2_RIDER]`.  The validator refuses a spec without the
   fifo rider.  Mark it `'unpinned'` ([[perf-probes-must-be-unpinned]]).
4. Run it with `--n-batches 3 --workers 4 --no-analyze` and the reference catalogue flags.
   It takes ~10 min of parent setup plus ~15 min of units.
5. Rank the `.prof` files by `total_tt`.  The ~0.8 s ones are pool helpers; the top four
   are the units.  Compare cumulative times per function name across snapshots.

The profiler roughly doubles walls; read the shares and the before/after deltas, not the
absolute seconds.  A patch script's assert firing on a snapshot that already has the hook
is harmless: it means the hook is there.
