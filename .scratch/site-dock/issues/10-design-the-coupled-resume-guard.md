# Design the coupled unit's resume guard

Type: grilling
Status: open
Blocked by: 02

HITL. Skills: `grilling`. Graduated out of the map's fog by
[Design the coupled work unit](02-design-the-coupled-work-unit.md), which settled the unit shape
this question was waiting on.

## Question

One coupled unit finalizes **two** leaves, so a mid-flight kill leaves a half-written pair. 02
settled that the unit carries `group_keys` and that a group finalizes only when every member uid
succeeded — so a crashed coupled unit finalizes *neither* leaf, which is the right default. What it
did **not** settle is what a subsequent `--resume` does with the wreckage.

1. **The completeness marker under two leaves.** `_finalize_config_run` (`supervisor.py:24-54`)
   writes one `sim_meta.json` per `run_dir`, and `runlayout.py:133` makes it the per-channel
   completeness marker. The skip guard in `_build_work_units` (`workunits.py:888-892`) skips a
   channel-run whose `sim_meta.json` exists and whose `resume.pkl` is gone. Decide what a coupled
   unit checks: both leaves complete, neither, or a state in between — and what happens when the
   two disagree, which a crash between the two `_finalize_config_run` calls can produce.

2. **Whether it needs the uniform-grain refusal.** The inbound map's mechanism (a trailer
   checkpoint format was ruled out of scope; refusal-until-clean stands) may or may not extend
   here. Memory `resume-architecture-verified-sound` is the counterweight: a hard mid-flight kill
   already resumes to 272/272 from `--resume DIR` alone, so the bar is "does coupling break
   something that works", not "does coupling need a new mechanism". Establish which before
   designing one.

3. **The batch-level grain.** `_plan_strategy_start` (`workunits.py:380-395`) plans a per-strategy
   start from the advanced `_ckpt_*.pkl`, and a batch-level resume already refuses when a dock
   exists. A coupled unit always has a dock, so check whether that existing refusal already covers
   this case — it may be that the answer is "nothing new", which is a legitimate and cheap
   resolution.

4. **The two leaves' starts.** If a batch-level resume ever were lawful here, the two leaves would
   have to restart at the **same** batch, because one batch is one site day
   (`staffing.py:723-727`). Decide whether that is an assertion or a refusal.

Related traps: `verify-tree-uses-the-runs-own-contract` (fixing a contract never rescues a finished
run), `run-end-writers-miss-the-final-flush` (a once-at-run-end writer inside `if pb:` does not fire
when `n_batches` divides the checkpoint cadence).
