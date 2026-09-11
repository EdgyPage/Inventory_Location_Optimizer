# Close the torn-finalize window

Type: task
Status: open

AFK. The execution override (map Notes) graduating two byte-identical precursors out of
[Design the coupled unit's resume guard](10-design-the-coupled-resume-guard.md) section 6. No
decision is open here; both are settled in that ticket and this one adds none.

**Both are byte-identical outside a crash window** — no run that completes without being killed
writes a different byte, and no `--resume` reaches either state today. Each becomes a silent wrong
answer the moment coupling lands, which is why they go in ahead of the reconciler. Same shape as
[Harden the three positional seams](11-harden-the-positional-seams.md),
[Seat the put-pool injection seams](12-seat-the-put-pool-seams.md) and
[Seat the one-owner bundle indirection](13-seat-the-one-owner-bundle-indirection.md).

## Question

Two changes in one file, on the same failure, in this order.

1. **`_finalize_config_run` writes its completeness marker LAST.**
   `Optimization/simdriver/supervisor.py:30-32` removes `resume.pkl`, `:33` cleans the
   per-strategy `_ckpt_*.pkl`, and only `:49` writes `sim_meta.json`. A kill anywhere in that
   window leaves a directory that is **neither resumable nor complete**: the skip guard
   (`workunits.py:892-895`) sees no `sim_meta.json` and declines to skip, `_load_resume` returns
   `None` because `resume.pkl` is already gone, and `_plan_strategy_start:66-69` therefore takes
   the fresh-run branch — `create_run` on a populated DB, **no `reset_strategy_db`**.

   Reorder to: write `sim_meta.json`, then remove `resume.pkl`, then clean checkpoints. The window
   then holds a dir with **both** files present, which today's guard already reads as *not*
   complete and which `_plan_strategy_start` already handles as its documented "resumed done arm"
   case (`ckpt == n_batches` → reuse `prev_id`, start `n_batches`, empty loop, re-finalize). The
   failure becomes fail-safe with no new state and no new code path.

   Keep the existing merge semantics intact: the read-back of a prior `sim_meta.json` (`:38-48`)
   must still happen before the write, so an additive run's strategy lists still merge.

2. **`_plan_strategy_start` refuses to `create_run` over an existing run.**
   `Optimization/simdriver/workunits.py:66-69` is the fresh-run branch. It calls `init_run_db` and
   `create_run` without checking whether the DB already holds a run for this strategy. Nothing
   reaches it in that state today, and after
   [Design the coupled unit's resume guard](10-design-the-coupled-resume-guard.md) sections 1-2
   the coupled reconciler will not either — but the invariant is true only by argument, and this
   map adds a second caller of that branch.

   Raise, naming the run dir, the strategy key and the existing `run_id`. The message should say
   the caller was expected to have reset the DB.

   **Why it is worth three lines:** `find_run` (`Optimization/persistence/Picking_Data.py:1935`,
   `:1940`, `:1944`) resolves `ORDER BY run_id LIMIT 1` — the **oldest** run. A DB that acquired a
   second run answers every `run_id`-filtered query from the **abandoned** one and doubles every
   unfiltered aggregate over the file. The corruption has no symptom; this is the only place it
   can be named.

## Acceptance

- A run's output tree is byte-identical before and after, on a store-only run and on a mixed one —
  the ordinary completion path writes the same bytes in the same files.
- **Item 1**: a test kills between the two writes (monkeypatch the `resume.pkl` removal to raise)
  and asserts the resulting dir is `sim_meta.json` present **and** `resume.pkl` present; then that
  a `--resume` over it reuses the prior `run_id`, runs an empty loop, and leaves the DB with
  exactly **one** run. Mutation-check it: revert the ordering and the test must fail.
- **Item 2**: a test plants a populated `sim_<key>.db` and asserts the fresh-run branch raises with
  a message naming the dir and the strategy. A second test asserts the ordinary fresh path — empty
  or absent DB — still returns `(run_id, 0)` unchanged.
- Two separate commits on `develop`, one per item: they share a failure but not a file, and item 1
  changes crash-window behaviour on flag-off runs while item 2 adds a refusal.
- Gates: `python -m pytest Tests/unit -q` plus the four `context/` verifiers touched by nothing
  here — run them anyway, per the map's standing practice.

## Not in scope

The coupled reconciler itself (`_reconcile_coupled_unit`, the two-leaf completeness test, the
torn-pair repair and the site-DB reset) stays in the map's fog until a coupled work unit exists to
reconcile. Every one of its decisions is settled in
[Design the coupled unit's resume guard](10-design-the-coupled-resume-guard.md) sections 1-5 and 7;
none of them is re-opened here.
