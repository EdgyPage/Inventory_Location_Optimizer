# Design the coupled unit's resume guard

Type: grilling
Status: resolved
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

## Answer

**Both leaves or neither; a torn pair REPAIRS rather than refuses; and the existing
strategy-granularity reset is the whole mechanism, widened from an arm to a unit.** The ticket
asked whether coupling needs a new resume mechanism. It does not — but its premise is wrong in
both directions, and the correction is where the work is: sub-question 3's refusal **already
fires** (twice, for reasons that have nothing to do with coupling), while sub-question 1's hazard
is **not the marker at all** but what a re-run does to a leaf that already finalized.

### Sub-question 3 answers itself — but only by accident

`_plan_strategy_start` (`workunits.py:78-95`) already raises on batch-granularity resume when
`roll_over or receiving`. Both are true of every coupled unit today:

- `receiving = recv_crew_spec(size=_recv_size) is not None`, where `_recv_size` is the **derived**
  site crew (`workunits.py:368`). `inb_off` clears `trailer_type`, `standing_yard`, `door_team`
  and the lead knobs (`whatif_config.py:197-200`) — it does **not** clear `recv_crew_size`, and
  under the era the size is derived rather than declared. So the anchor cell has a receiving crew
  like every other cell.
- `roll_over` is on under the era (memory `nothing-is-lost-under-the-era`).

So the live answer to "does the existing refusal cover this" is **yes**. That is not a reason to
leave it alone — see section 4.

### 1. The completeness test: both leaves, no new marker

**A coupled unit is complete iff BOTH leaf dirs are complete** by today's test —
`sim_meta.json` present and `resume.pkl` absent (`workunits.py:892-895`). Nothing new is recorded.

The reason a marker is unnecessary is an implication that already holds: `_finalize_config_run`
runs only when `members <= done_uids` (`supervisor.py:126`), so **two finalized leaves cannot
exist without a successful unit**. The pair of markers already carries the fact a third marker
would record.

**Rejected: a pair-level marker in `<pair>/_site/`.** It costs an artifact declaration and a
contract bump to record a derivable fact, and — decisively — **it can tear in its own right**. A
kill after both leaves finalize but before the marker is written leaves a unit that is complete by
every observable and incomplete by the marker, which re-runs a **finished** pair. Adding a third
thing to keep in sync is not how you fix two things being out of sync.

**Rejected: refusing to resume a coupled run.** Memory `resume-architecture-verified-sound` — a
hard mid-flight kill already resumes to 272/272 from `--resume DIR` alone. The bar this ticket set
itself was "does coupling break something that works", and throwing the working thing away is not
an answer to it.

### 2. The torn pair repairs, and says so

A kill between the two `_finalize_config_run` calls leaves one leaf complete and one not. That
pair **repairs**: the orphaned complete leaf is treated exactly as a partial arm —
`reset_strategy_db` plus removal of its `sim_meta.json` — and both leaves replay from batch 0, at
`log.warning`.

**The replay is forced, not chosen.** The coupled worker runs one batch loop over both leaves
([02](02-design-the-coupled-work-unit.md) section 2) across a shared dock, a shared put clock
([04](04-design-the-site-put-away-pool.md), amending 02 section 5) and a shared `recv_clock`. Leaf
B cannot be stepped without leaf A being stepped, so there is no version of this where the
complete leaf is spared. The only question was whether its existing output is discarded cleanly or
left to collide with the replay — and that question has one answer.

**The repair is exact, which is why refusal buys nothing.** Strategy-granularity resume already
resets a partial arm's DB and replays it **bit-identically** from batch 0
(`workunits.py:72-77`). Un-finalizing a complete leaf is that same operation applied to a leaf
that happened to reach the end. This is the difference from the inbound map's out-of-scope
trailer-checkpoint refusal: there, no exact replay existed, so refusal-until-clean was the only
honest option. Here one does.

**It is not silent.** Memory `pool-run-swallows-dead-arms`: `run.log` is the only place a
multi-hour run's damage is visible, and "a finished leaf was discarded and replayed" is a line a
reader must be able to find.

### 3. The site DB is the unit's third output

`reset_strategy_db` (`strategy_runner.py:432-442`) knows `sim_<key>.db`, its keyframe sibling and
`_ckpt_<key>.pkl`. It does **not** know
`<cell>/<pair>/_site/inbound_<arm_store>__<arm_ful>.db` — the site DB
[03](03-design-the-site-scope-in-the-run-tree.md) section 1 created — so a replayed coupled unit
would append a second run's trailer, drain and door rows to it. The reset extends to it.

**It is the only such artifact.** 03's section 2 table puts every pack-denominated receiving
quantity in the owning channel's own sim DB, and `run_layout.json`'s coupled marker is written by
the parent, once per run, not per unit. So the reset surface is exactly three things per leaf plus
one per unit.

**Rejected: making the site DB idempotent per unit key.** It needs a stable key inside the DB and
a delete path in the writer, for the same guarantee — and it puts the cleanup in the **worker**,
where `reset_strategy_db`'s own docstring says it must not be: the reset "runs in the PARENT
before any worker reopens the file (Windows-safe)" (`strategy_runner.py:435`).

### 4. `coupled` becomes its own refusal reason — and sub-question 4 dissolves

The batch-grain refusal gains a third, independent `why`. Not because the current two fail today,
but because **neither is a statement about coupling**: `receiving` evaporates if a derived crew
ever rounds below 1, and `roll_over` is a work-day knob any cell may clear. A safety property that
holds by coincidence is one nobody will notice losing.

The coupled reason is also the strongest of the three. Two leaves resume from two independently
written `_ckpt_<key>.pkl` files in two directories, so a batch-level resume could lawfully start
them at **different batches** — and one batch is one site day (`staffing.py:723-727`, which
refuses channels with differing batch counts outright). A site day half-run in one channel and
not the other is not a degraded run; it is a run whose shared dock, put pool and recv clock never
existed.

**So sub-question 4 is answered by REFUSAL, not by an assertion.** The ticket asked whether the
two leaves' equal start is an assertion or a refusal. There is no lawful two-leaf batch start at
all, so there is no downstream code that should be written to assert about one. Asserting would
imply the case is reachable.

### 5. Where it lives

**`_reconcile_coupled_unit(...)` — a named module-level function in `workunits.py`**, called from
`_build_work_units` where the skip guard sits today. It answers "is this unit complete", and
repairs as a side effect.

Per-leaf resume planning lives **inside** `_prepare_channel_run` (its `_plan_strategy_start` loop,
`workunits.py:373-384`), so a two-leaf reconciliation has to happen above both prepares; and the
reset must be parent-side before any worker opens a file. `_build_work_units` is the only place
that satisfies both.

**Rejected: inlining it** like today's one-line guard — three moving parts (two leaf states, a
repair, a site-DB removal) in the middle of a loop already doing staffing derivation, coverage
recording and prepare dispatch. **Rejected: a new `resume_guard.py`** — ceremony for one function,
when `workunits.py` already owns `_plan_strategy_start` and the existing guard. Completeness is one
topic and stays in one file.

### 6. Two byte-identical precursors, takeable now

Graduated to [Close the torn-finalize window](16-close-the-torn-finalize-window.md).

**(i) The finalize write order.** `_finalize_config_run` removes `resume.pkl`
(`supervisor.py:30-32`), cleans checkpoints (`:33`), *then* writes `sim_meta.json` (`:49`). A kill
in that window leaves a dir that is neither resumable nor complete — and that dir lands on the
duplicate-run path in (ii). Writing `sim_meta.json` first makes the window **fail-safe**: the dir
then reads as meta-present-and-resume-present, which today's guard correctly calls *not* complete,
and which `_plan_strategy_start` already handles as the documented "resumed done arm" case
(`ckpt == n_batches`, reuse `prev_id`, empty loop). A flag-off crash window, so it does not ride a
coupled commit.

**(ii) The duplicate-run guard.** `_plan_strategy_start:66-69` calls `create_run` on a DB that may
already hold a run for this strategy, with no reset. After sections 1-2 the coupled path cannot
reach that state — but nothing *states* it, and this map is about to add a second caller. Three
lines at the point of no return, raising and naming the dir.

Both are byte-identical outside a crash window and need no coupled unit, which is the same shape
as [Harden the three positional seams](11-harden-the-positional-seams.md),
[Seat the put-pool injection seams](12-seat-the-put-pool-seams.md) and
[Seat the one-owner bundle indirection](13-seat-the-one-owner-bundle-indirection.md).

The reconciler itself (sections 1-5) stays in the map's fog: the question is sharp, but it cannot
be written until a coupled unit exists to reconcile.

### 7. How it is proven

**A planted four-state matrix, checked by mutation, plus one fault-injected torn tree.**

The reconciler is a pure function of an on-disk tree, so the four two-leaf states — both complete,
both partial, A complete/B partial, neither — are plantable by hand, each asserting the skip
decision **and** the exact filesystem effect (which files were removed, which survived). Each
branch gets a sabotage: [09](09-extract-the-one-leaf-coordinator.md) is the standing lesson that a
behavioural-equivalence test can pass on mutated code when the scenario never exercises the thing,
and memory `real-test-coverage-is-317` is why a pass count is not evidence.

But a planted matrix only ever proves the reconciler reads a tree **I wrote**. One fault-injected
case ties it to reality: monkeypatch `_finalize_config_run` to raise on the second call in a small
end-to-end run, then resume, and assert the tree it left matches the planted A-complete/B-partial
state. That single case is what makes the other four mean anything.

### Findings

1. **`find_run` resolves the OLDEST run, not the newest.** `Picking_Data.py:1935`, `:1940`,
   `:1944` are all `ORDER BY run_id LIMIT 1`. So a DB that acquired a second run would have every
   `run_id`-filtered query silently answered from the **abandoned** one, and every unfiltered
   aggregate over the file doubled. This is what makes section 6(ii) worth three lines: the
   corruption has no symptom.

2. **A one-leaf version of the torn window exists today**, on flag-off runs — the write order in
   section 6(i). Not reachable through `--resume` (see finding 4), but reachable by a kill.

3. **A fourth positional seam, not in "Harden the three positional seams".**
   `supervisor.py:108-110` does `meta[gk]['sim_skeleton']` with `gk = uid[:3]` (`:86`) and matches
   `_s.get('key') == uid[3]` to attach `expected_pick`. [02](02-design-the-coupled-work-unit.md)
   settled `uid[:3]` for the *finalize* path by carrying `group_keys`; this block slices
   independently, earlier. Under the coupled uid `(label, 'coupled', arm_store, arm_ful)`,
   `meta[gk]` is a **KeyError** inside the success `try`, so a unit that **succeeded** is logged
   `strategy FAILED` and never finalizes. Unlike 11's other three this one is not silent — it is
   loudly *mis-attributed*, which is its own failure mode. Amended into
   [Harden the three positional seams](11-harden-the-positional-seams.md).

4. **`_finalize_config_run`'s additive-run merge describes a path the skip guard blocks.** Its
   docstring (`supervisor.py:36-39`) handles "a `--resume` run adding a NEW strategy into a prior
   comparison dir" — but `skip_completed = resume` (`scenario.py:182`), so under `--resume` a
   finalized dir is skipped entirely and never reaches a prepare. The merge is dead on that path.
   Harmless, and not this map's business; recorded so the next reader does not take the docstring
   as evidence the state is reachable.

### What this ticket hands onward

- **[Close the torn-finalize window](16-close-the-torn-finalize-window.md)** — sections 6(i) and
  6(ii), takeable now.
- **[Harden the three positional seams](11-harden-the-positional-seams.md)** — amended with
  finding 3.
- **The map's fog** — the reconciler itself, beside the other coupled builds.
- **No glossary term and no ADR.** "Torn pair", "reconcile" and "complete" are harness states, not
  domain vocabulary, and `CONTEXT.md` is implementation-free by construction. Repair-vs-refuse is a
  real trade-off but a cheaply reversible one, internal to the run harness, with no consequence for
  any reader of a result; [ADR-0005](../../../docs/adr/0005-inbound-scope-splits-at-the-pack.md)
  already covers the scope decision this sits inside.
