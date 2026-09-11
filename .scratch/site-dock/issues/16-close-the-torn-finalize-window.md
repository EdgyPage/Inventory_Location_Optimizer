# Close the torn-finalize window

Type: task
Status: resolved

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


## Answer

Both items built as specified, in the given order, as two commits on `develop`.

### 1. The marker is written first — `1b2c572f`

`_finalize_config_run` (`Optimization/simdriver/supervisor.py:24`) now writes `sim_meta.json`,
then removes `resume.pkl`, then cleans the checkpoints. The crash window holds a dir with BOTH
files: the skip guard reads it as *not* complete and re-plans it, `_load_resume` still answers,
and `_plan_strategy_start` takes its documented resumed-done-arm branch. No new state, no new
code path, only the sequence. The additive-run read-back stayed AHEAD of the write, unmoved —
only the two removals crossed the pair.

### 2. The fresh branch refuses a second run — `66515339`

`_plan_strategy_start` (`Optimization/simdriver/workunits.py:67`) checks `find_run` before
`init_run_db`/`create_run` and raises naming the db, the strategy key, the run dir and the
existing `run_id`, saying the caller was expected to have reset the arm's DB. `find_run` was
already the rename-proof accessor for exactly this question, so the check is three lines and one
new import from a module `workunits` already imported two symbols from.

**The refusal is reachable only through a bug, and that was confirmed rather than assumed.** A
new run always mints a fresh timestamped `base_dir` (`run_simulation.py:958`), and `is_resume` is
`resume is not None` at the call site (`workunits.py:380`), with `skip_completed=resume`
(`scenario.py:182`) — so a `--resume` either skips a finalized channel-run or finds its
resume.pkl. The one way in was the torn window item 1 closes.

## What proved it

- **Mutation-checked both ways.** Reverting the ordering fails
  `test_a_kill_inside_finalize_leaves_the_dir_resumable` on the missing marker (the removal
  raises before the write); removing the refusal turns
  `test_fresh_branch_refuses_to_create_a_second_run` into `DID NOT RAISE`.
- **Three tests in `Tests/integration/test_crash_recovery.py`**, which already owned the finalize
  gate and the `_plan_strategy_start` branches. They use a REAL `sim_uni.db` (`init_run_db` +
  `create_run`) rather than a fake, so `_n_runs(db) == 1` is a genuine assertion about the file:
  the torn test asserts both markers, then that the resume reuses `prev_id`, runs an empty loop
  and leaves exactly one run; the refusal test asserts the message carries all four facts and
  that no second run was left behind; the third asserts the ordinary fresh path is unchanged on
  BOTH shapes of a virgin arm — an absent DB and an empty one carrying only the schema.
- **Gates green:** `Tests/unit` 2037 passed; `Tests/integration` 409 passed, 1 skipped;
  `Tests/e2e` 31 passed, 1 skipped (it is where `_finalize_config_run` is actually driven —
  `test_channel_runner_smoke`, `test_production_hours_e2e`, `test_standing_yard_e2e`). Nine
  verifiers green.

## Findings

- **The ticket's gate line was wrong, and the correction is cheap but not free.** Both edited
  files are in `contract.SHAPE_SOURCES` (`Optimization/runschema/contract.py:62-63`), so ANY edit
  to either — a comment included — moves the run-tree source fingerprint and reddens
  `preflight --check`. The fix is not a code change but the full `python -m Optimization.runschema.preflight`,
  which proves the shape with two canary runs (~83 s) and refreshes the fingerprint in
  `Optimization/schemas/run_tree/INDEX.json`. `verify_architecture` goes red for the same reason
  (the graph hashes source). Two of the nine gates are owed by every commit this map makes to a
  simdriver file; budget them.
- **Those canaries ARE the byte-identical acceptance.** Preflight ran a mixed-catalogue 2-cell
  sweep and a store-only single cell through the reordered finalize and reported the tree shape
  UNCHANGED on schema `5c9bc35db55b`, with the same level sets. A literal byte-diff of two run
  trees is not obtainable (timestamps in the dir name and in `simulation_runs.created`), and the
  reorder moves three filesystem operations with no data dependency between them — the merge, the
  content and the destination of `sim_meta.json` are untouched, and the test asserts the merge
  still precedes the write.
- **`Tests/architecture` is red on HEAD, and this change adds nothing to it.** Baselined against a
  `git archive 10e7f81e` copy: four failures stand pre-existing
  (`test_key_backbone_edges_present`, `test_the_dead_site_is_still_dead`,
  `test_no_new_handwritten_contract_paths`,
  `test_every_loader_that_reads_a_conditional_table_is_declared`). A fifth,
  `test_every_consumer_that_declares_requirements_is_validated_here`, fails ONLY in a working tree
  carrying `.claude/worktrees/` — every unvalidated declaration it lists is a path inside two
  stale worktrees from earlier sessions, which the archive copy does not have. Memory
  `arch-tier-is-red-on-head` already records both halves of this.

## Not built here, unchanged

The coupled reconciler (`_reconcile_coupled_unit`, the two-leaf completeness test, the torn-pair
repair and the site-DB reset) stays in the map's fog. Its decisions remain settled in
[Design the coupled unit's resume guard](10-design-the-coupled-resume-guard.md) sections 1-5 and
7; nothing here re-opened one.
