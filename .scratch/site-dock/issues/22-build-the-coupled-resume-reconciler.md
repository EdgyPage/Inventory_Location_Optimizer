# Build the coupled resume reconciler

Type: task
Status: resolved

AFK. **Takeable now** — graduated from the map's "remaining builds" fog by the execution
override (map Notes). [Design the coupled unit's resume guard](10-design-the-coupled-resume-guard.md)
settled the rule; its two byte-identical precursors landed as
[Close the torn-finalize window](16-close-the-torn-finalize-window.md), and
[Build the coupled work unit](18-build-the-coupled-work-unit.md) made the pair it reconciles
exist. Nothing else on this map blocks it and it blocks nothing, so it runs alongside
[Build the site space view](20-build-the-site-space-view.md) and
[Re-shape the funnel spec for arm pairs](23-reshape-the-funnel-spec.md).

## Question

Build 10's answer, the coupled half: `_reconcile_coupled_unit`, the two-leaf completeness test,
the torn-pair repair with its `sim_meta.json` removal and leaf reset, the site-DB arm of that
reset, and the `coupled` refusal reason for batch-grain resume.

Two facts from later tickets that the design predates:

- **`run_layout.json` carries `coupled` and resume restores it** (18). A run that resumed without
  it would rebuild per-channel units over a tree whose leaves were written by coupled ones, so
  the marker is the reconciler's ground truth for "was this pair coupled", not an inference from
  the uid.
- **A unit returns one result PER LEAF** (18), and `group_keys` and `leaves` are positional with
  a length check. A coupled unit's failure already blocks BOTH leaves from finalizing, which is
  the completeness property this reconciler is repairing around rather than establishing.

## What proves it

- **The planted four-state matrix**, with its mutation sabotages and **one fault-injected torn
  tree** — a hard mid-flight kill, not a hand-built directory. Memory
  `resume-architecture-verified-sound` is emphatic that the "strategy-level reset" log line is
  NOT evidence of a working resume; only a real kill-and-resume is.
- **Flag-off and uncoupled unchanged, MEASURED**: both preflight canaries, plus a row-level diff
  against a `git archive HEAD` copy on a resumed uncoupled run.
- Nine verifiers; `Tests/architecture` baselined by diffing the failure LIST against a
  `git archive` copy, never the totals (memory `arch-tier-is-red-on-head`).

## Answer

**BUILT, in the working tree.** `_reconcile_coupled_unit` is a module-level function in
`workunits.py`, called from `_build_work_units` where the one-line skip guard sat; it answers
"is this pair complete" and repairs a torn pair as a side effect. Batch-grain resume now refuses
a coupled leaf for its own named reason. Both are proven by a planted four-state matrix under
mutation and by a **real** mid-flight kill through the production pool, and the flag-off path is
measured byte-identical over 410,338 rows.

10's design held in full — nothing in it had to be revised. What it did not see is a **second**
torn state that the same repair fixes, and which wedges a coupled run permanently without it.

### The state 10 did not see: the checkpoint skew

10 reasoned about the window between the two `_finalize_config_run` calls. There is a second,
**strictly more reachable** window one level down. Each leaf writes its own `_ckpt_<arm>.pkl`
from inside the one batch loop — `strategy_runner` runs `for lf in leaves: lf.step(i)`, and
`finish()` is likewise per leaf — so a kill between leaf A's save and leaf B's leaves the pair
one checkpoint apart. The final save is the worst case: `finish()` pins the marker to
`n_batches`, so a kill right there leaves leaf A **done** and leaf B at its last cadence
boundary.

That state does not merely degrade a resume, it **wedges the run**:

- leaf A's arm is planned on the documented done-arm branch — reuse `prev_id`, start
  `n_batches`, never reset;
- leaf B's arm is `0 < ckpt < n_batches`, so strategy granularity resets it to 0;
- `_run_strategy_worker_impl` then refuses the unit ("the leaves of a work unit must share one
  batch range"), which is correct and is 18's guard doing its job;
- and the next `--resume` reproduces it exactly: A's checkpoint was never touched, B's is gone
  and its resume counter was rewritten to 0 by the prepare that just failed. Every subsequent
  resume refuses in the same place, forever.

So the reconciler's rule is stated one level finer than 10 phrased it: **the two leaves must
agree, arm for arm**, and any rank that disagrees is discarded in *both* leaves. The torn-finalize
case is then the degenerate form of the same rule — a finalized leaf has had its checkpoints
cleaned, so no position survives to compare and the whole directory replays. One repair, two
entry conditions, and the sub-question-4 refusal 10 designed stays exactly where 10 put it (the
*batch* grain), because no assertion anywhere claims the starts agree.

### 1–5, as built

1. **The completeness test.** `_leaf_is_complete` — the marker present, the resume file absent.
   No new marker, for 10 section 1's reason. The **uncoupled** skip guard now reads the same
   function: the coupled rule is that same test over two directories, and two spellings of
   "complete" would be two things to keep in step over exactly the question being reconciled.
2. **The torn-pair repair.** Whole-dir on both leaves: `reset_strategy_db` per arm, the marker
   removed from whichever leaf carries it, at `log.warning` naming the pair and the count.
3. **The leaf reset needed a third removal 10 did not name** — see Deviations.
4. **The site-DB arm.** `_site_db_path(pair_dir, arm_store, arm_ful)` states 03 section 1's path
   in one place, and every discarded rank's site DB is removed with the leaves' three files.
   Nothing writes it yet (that is 21/24), so the branch is a live no-op today and the test
   plants the file to prove the branch is not dead.
5. **The `coupled` refusal.** `_plan_strategy_start(..., coupled=False)`, checked *first* of the
   three, with its own `why`; `_prepare_channel_run` carries the flag and `_prepare_site_run`
   sets it on both leaves. Default False, so every uncoupled caller is unchanged.

### Deviations under force

- **`resume.pkl` had to join the reset, and 10's section 3 does not list it.** 10 counted the
  reset surface as "three things per leaf plus one per unit". It is **four**. `reset_strategy_db`
  removes an arm's db, its keyframe sibling and its checkpoint — it cannot reach the resume
  record, and that record is the planner's *fallback*: `_arm_position` reads the checkpoint `or
  prev_start`. An arm reset without forgetting its counter is therefore planned at the counter
  the last prepare wrote — typically `n_batches` — and runs an **empty loop over a database that
  no longer exists**. No exception, no log line, no rows. `_forget_arms` drops the reset arms
  from the record and removes the file when none survives. This is the failure
  `test_the_repaired_tree_plans_a_fresh_start_with_one_run_per_db` exists to catch, and it is
  the one mutation of the nine that a "did the files disappear" test could not have found.
- **A new `mid_flight` flag on `_build_work_units`, which 10 had no reason to anticipate.**
  `_supervise` widens `skip_completed` to True on every retry after a hard worker death, so the
  reconciler would also run *while the pool is up* — and there, every unit is already in
  `done_uids` and filtered out of the resubmission. A repair at that moment deletes the output
  of units nothing rebuilds, and the safety sweep then finalizes the leaves over empty
  databases. The supervisor now says which build it is (`mid_flight=attempt > 0`); a tear seen
  mid-flight is reported at `log.error` and left for the next `--resume`, where the units are
  planned again and the repair is safe. The pair is not skipped either, so its prepare hits 16's
  duplicate-run refusal — loud, and non-destructive.
- **The reconciler is per PAIR, not per unit, and the name is still right.** A coupled pair has
  exactly one config per channel (`_prepare_site_run` refuses otherwise), so the pair's two leaf
  directories hold every unit's leaves and the completeness marker is per directory. The
  function reconciles the arm ranks *within* those two directories, which is the unit-level
  question 10 asked, asked at the only scope where the marker exists.
- **`_site_db_path` builds a path by hand.** CLAUDE.md sends run-tree *consumers* through
  `resolver_for`, but 03's artifact is not declared yet — that contract bump rides the writer
  (site-dock 24), and declaring it here would mean editing the committed shape store, which this
  session does not own. This is the parent building a path under a directory it already holds,
  which is what `reset_strategy_db` does with the checkpoint. It is stated once so the writer
  reads it from here.

### Findings

1. **The run-tree ratchet is a prose ratchet, and heavy docstrings trip it.**
   `test_no_new_handwritten_contract_paths` counts raw text, comments included. The first draft
   of this work raised `workunits.py` from 3 to 6 `resume.pkl` mentions, 3 to 7 `sim_meta.json`
   and 0 to 2 `run_layout.json` — **entirely in documentation**, with no new hand-joined path
   except one. The gate is already red on HEAD, so the *totals* said nothing; only diffing the
   per-file list against a `git archive` copy showed it. Reworded to name each artifact once and
   consolidated onto `_meta_path`, the file now sits **below** its baseline on both tokens and
   drops out of the regression list entirely — a net shrink of two entries against HEAD.
2. **`_prepare_channel_run` had two spellings of an arm's db path.** The map it hands the
   workers and the reconciler's removal are the same file; a second spelling would not fail, it
   would remove nothing and leave the rows to be appended to. Folded into `_arm_db_path`.
3. **A coupled unit's two leaves do not in fact resume from "two independently written
   checkpoints" in the sense 10 feared** — they are written in one loop, by one process, a few
   microseconds apart. The refusal 10 designed is still right, but the reachable hazard is the
   *microsecond* gap, not a divergence of grain. That is why the skew is a repair rather than a
   second refusal.
4. **The `coupled` refusal stands on nothing today, which is the point.** The e2e fixture runs
   with no receiving crew, and the era's other two reasons are off; with the coupled reason
   removed (mutation M6), a coupled leaf accepts a batch-grain resume silently. 10 predicted
   exactly this ("a safety property that holds by coincidence is one nobody will notice
   losing") and the mutation measures it.

### What proves it

- **The planted four-state matrix** plus the skew, the resume-record forget, the live-retry
  exemption, the site-DB path shape and the refusal — 14 tests in
  `Tests/integration/test_coupled_resume_reconciler.py`, each asserting the exact filesystem
  effect rather than only the return value.
- **The fault-injected torn tree is a real kill**, `Tests/e2e/test_coupled_resume_e2e.py`: a
  coupled pair driven through `_run_workers_flat` with a real `ProcessPoolExecutor` and real
  spawned workers, with `_finalize_config_run` raising after the first leaf. The tear is then
  **read off the tree**, not asserted from the patch. The resume is checked three ways, none of
  them a log line (memory `resume-architecture-verified-sound`): both leaves finalize; every arm
  db holds exactly **one** run; and every row of the replayed pair is **identical to a coupled
  run that was never killed**, which is the exactness claim the whole repair rests on.
- **Flag-off measured, not argued.** `git archive HEAD` into a clean copy, a second copy with
  *only* this ticket's two source files laid over it (the working tree carries two other
  sessions' work), and a resumed **uncoupled** mixed run driven through the production pool in
  each — killed at the finalize window, then resumed. 4 arm DBs, 10 non-empty tables each,
  **410,338 rows**: the two fingerprints are byte-identical, and the run genuinely resumed (one
  marker after the kill, two after). Counted before trusting, per the empty-table trap.
- **Mutation-checked: 9 planted, 9 caught.** Completeness by `any` instead of `all`; the repair
  touching only the first leaf; the skew never detected; the resume record not forgotten; the
  site DB surviving; the `coupled` refusal removed; the mid-flight decline removed; the site
  leaves prepared as uncoupled; the supervisor never declaring a retry mid-flight.
- **Gates.** 2,571 unit + integration passed (14 new), 42 e2e + 1 skipped (1 new), six read-only
  verifiers green including `runschema.contract --check` (no contract movement).
  `Tests/architecture` baselined by LIST against the `git archive` copy: the six new failures are
  all "the arch chain has not been re-run" (graph, nodes ×3, architecture sync, files catalog),
  which is this map's standing hand-off; the ratchet's list **shrank**.

### What this hands onward

- **The arch chain and the two preflight canaries are NOT run here.** This session shared the
  tree with two others; `context/files.yml`, `docs/architecture/**` and `Optimization/schemas/**`
  are the parent's to regenerate once. Two new files need catalogue entries:
  `Tests/integration/test_coupled_resume_reconciler.py` and `Tests/e2e/test_coupled_resume_e2e.py`
  (`--catalog-merge` has seeded a docstring FRAGMENT as `purpose` for new files twice on this
  map — 11's and 18's finding — so check them by hand rather than grepping for `TODO`).
- **Site-dock 21 / 24 inherit a live seam.** `_site_db_path` is the one spelling of 03's site DB;
  the writer should read it from there, and declaring the artifact (with its contract bump) is
  what lets the removal move onto a `resolver_for` accessor.
- **A third leaf would raise, deliberately.** The site-DB stem is two-armed by construction, so
  a hypothetical three-channel site fails at the name rather than silently filing under two of
  the three.
