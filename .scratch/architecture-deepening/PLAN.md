# Architecture deepening -- the plan

Label: wayfinder:plan
Opened: 2026-09-16
Tracker: `map.md` beside this file holds Destination / Decisions / Fog. This file holds the
ordered work, what each step must produce before it counts as done, and the gates each asks for.

---

## What this plan is for

Four explorers walked the ten-week hot spots and found the same shape sixteen times: **one fact
declared in N places, with no module at the seam**. Two of those declarations have already drifted
into live defects, and one of the two is read by a scoring expression in an arm that appears twice
in the staged phase-2 campaign.

The organizing decision is the run. A fresh campaign re-baselines every number, so the moment
before a launch is the cheapest this repo will ever have to take a comparability break -- and the
most expensive moment to churn the driver. So the split is sharp: **one correctness ticket before
the launch, every structural ticket after it, in a HEAD copy.**

---

## Phase 0 -- baseline (before any edit)

Not optional, and it is cheap. Four of the six surfaces this arc touches already carry a red
architecture test that is not ours, and `Schema.profile_tree --check` is red on HEAD.

1. Run all ten gates from `CLAUDE.md` §1 and record pass/fail per gate in §Progress. Do NOT trust
   a green architecture tier without confirming `pyyaml` and `coverage` are installed -- without
   pyyaml, 7 of 31 `Tests/architecture/*` files `importorskip` and vanish, and they are exactly
   the sync gates.
2. Record the three placement equivalence files' pass state (~4 s) as the pre-run baseline.
3. ~~Take a `git archive HEAD | tar -x` control copy~~ -- superseded. A **toy run** is the
   better instrument and was built instead: `smoketest.py --profile tiny`, 4.5 min at 12
   workers, 136 arms, every structural feature. Two HEAD-vs-HEAD runs digest IDENTICAL, and
   `run_digest.py --self-test` detects planted 1e-9 damage, so the instrument can fail. The
   archive copy is only needed while a real campaign is running.

**Exit:** a per-gate before table exists in §Progress, with the known-red ones named against their
`.scratch/architecture-drift/` ticket.

---

## Phase 1 -- PRE-RUN, and nothing else (ticket 01)

The only work that touches `develop` before the campaign launches.

**Ticket 01** -- `_aisle_vol_sum` symmetric decrement + a non-vacuous test.

**Exit:**
- The decrement lands in `_drop_sku_from_aisle` AND its inline twin in `_reclaim_empty_bins`.
- A test asserts the MAGNITUDE of the decrement, not merely that it moved, on both paths.
- ~~The three placement equivalence files are re-baselined~~ -- **this expectation was wrong.**
  They pass untouched (95 tests): an agreement pin between a family's pool and wave halves does
  not notice a fix to the shared state both halves read. Carry the corollary into phase 2 -- those
  suites would not have caught this drift either, so a green equivalence run is not evidence that
  a ledger quantity is correct.
- Gate 10 (~20 s) green; `path_guard` and `docref_guard` green.
- The commit says plainly which published results it invalidates: `rank_cartlabor` fulfillment
  arms, bounded to aisles that crossed cart capacity.

**Then the campaign launches.** Detached, no console, with a keep-awake -- Modern Standby logs
506/507 and kills a run with STATUS_IN_PAGE_ERROR (memory `launch-long-drivers-detached`). Sizing
is ~13 h at 4 workers, ~9 h at 6.

---

## Phase 2 -- the placement arc (tickets 02-05)

First after the run, in the HEAD copy. `Warehouse/placement/` is the one surface with no red test
standing over it and a 4-second equivalence suite, so this is where to build confidence. All four
tickets touch `Assignment_Functions.py`; doing them as one pass means one re-run of the oracles
instead of four.

| Ticket | What |
|---|---|
| 02 | `AisleLedger` -- eleven dicts, one `add`/`drop` pair, `reconcile()` |
| 03 | `PlacementPolicy` record; delete 3 dead registries, the `load_*` family and `lift_sum` |
| 04 | Collapse the ranked `_impl` twins into drivers over the pools |
| 05 | The cold-start tie-break quadratic (folded from `complexity-round/17`) |

**Exit:** equivalence suite green and byte-identical for 02 and 04; `run_digest.py` proves DB-row
neutrality where a ticket claims it (never PNGs -- memory `figures-are-not-byte-reproducible`);
gates 1, 2, 7 and 10 green; `resolver_hints.yml` declares the `open_pool -> take` edge that 14 of
17 arms actually take.

---

## Phase 3 -- the driver arc (tickets 06-08)

`strategy_runner.py` carries drift ticket 02 (a hot path never observed executing). Attribute
against the baseline, not the count.

| Ticket | What |
|---|---|
| 06 | `ArmAssembly` + `BatchState` -- split `_build_leaf` where construction stops |
| 07 | `CheckpointBuffer` -- the 13 lists, the channel table, `pending()` replaces `if pb:` |
| 08 | `LeafScope` -- two adapters replace 28 ternaries and 8 domain-layer refusals |

**Exit:** 06 lands first because 07 and 08 need somewhere to live. Gate 10 every commit; gate 2
mandatory for any new import edge; `test_calltree_anchors.py` names the exact `SECTION_MAP` entry
to fix when a hot-path symbol moves.

---

## Phase 4 -- config (tickets 09-10)

| Ticket | What |
|---|---|
| 09 | The `Knob` registry -- one declaration, six derived loops |
| 10 | Delete `GLOBAL_POLICIES` and its ten config sites |

**Exit:** gate 4 if `SHAPE_SOURCES` moves; watch `.scratch/architecture-drift/issues/06` -- the
hand-written-path ratchet has 11 recorded sites in `whatif_config.py` and this work should move the
count DOWN, which it permits.

---

## Phase 5 -- persistence and the read side (tickets 11-15)

`Picking_Data.py` carries drift ticket 07. Ticket 13 does not start until
`.scratch/architecture-drift/issues/05` has an owner.

| Ticket | What |
|---|---|
| 11 | One `Column` declaration per table; derive the other eight lists |
| 12 | The `Frame` table -- one cache, one accessor, nine kinds |
| 13 | One `ContractStore` behind the three copied shape stores |
| 14 | A span's four spellings collapse into `SECTIONS` |
| 15 | `state_at`'s four records become adapters; `Requires` gets enforced |

**Exit:** every DDL change rides the schema pipeline (`--sync` before, `--accept` after); gates 4,
5 and 6 as applicable; the `work_day` ratchet extends from 1 writer to 20 tables.

---

## Phase 6 -- the remainder (tickets 16-18)

| Ticket | What |
|---|---|
| 16 | `put_seconds_at` -- one at-bin cost expression for the sim and the objective |
| 17 | The put-away rung chain gets a seam |
| 18 | Re-judge the two inventory mixins (blocked by 02) |

**Exit:** 18 is allowed to end in "closed with a reason" and is marked as likely to.

---

## Progress

| Phase | Ticket | Status | Note |
|---|---|---|---|
| 0 | baseline | DONE | gate 6 red, pre-existing (drift 04); all others green |
| 1 | 01 | **RESOLVED** | PRE-RUN. vol_sum drift fixed, non-vacuity proved |
| 2 | 02 | **RESOLVED** | A: drop half. B: add half -- eleven commit blocks, one body, digest IDENTICAL. Remainder split to 21 |
| 2 | 03 | **RESOLVED** | + the PlacementPolicy record; FAITHFUL_GAIN_FAMILIES and the aisle_state lists derived |
| 2 | 04 | not started | **UNBLOCKED** by 03 |
| 2 | 05 | not started | blocked by 04 |
| 3 | 06 | part-landed | BatchState: 5 arm-scope names whose lifetime is one batch. **ArmAssembly -> 24** |
| 3 | 07-08 | not started | blocked by 06 |
| 4 | 09 | **RESOLVED** | Knob registry; write-back, record and resume restore derived |
| 4 | 10 | **RESOLVED** | GLOBAL_POLICIES deleted |
| 5 | 11 | not started | **UNBLOCKED** |
| 5 | 12 | **RESOLVED** | frame table + one cache + one accessor |
| 5 | 13 | not started | blocked by 11, and on architecture-drift/05 |
| 5 | 14 | not started | blocked by 07 |
| 5 | 15 | not started | blocked by 11 |
| 6 | 16 | **RESOLVED** | one at-bin put expression; the objective drops its term by name. File split -> 22 |
| 6 | 17 | **RESOLVED** | ADR-0003's five rungs become a chain; `mgr.put_chain` is the knob the ADR anticipated |
| 6 | 18 | **RESOLVED** | closed with a reason: all three mixins stay |
| - | 19 | **RESOLVED** | LoadParams deleted; init_lift_state renamed; delta_lift_idxs closed with a reason |
| - | 20 | **RESOLVED** | `aisle_metrics.pick_load_sum` deleted -- wrong on 15 of 17 arms, no reader |
| - | 21 | not started | NEW, split from 02B: the pool builders take loose dicts, not a ledger. Ride ticket 04's oracle re-freeze |
| - | 22 | not started | NEW, split from 16: `Inbound/gain.py` is 1,398 lines and four modules |
| - | 23 | **RESOLVED** | the seed prices only the arm's terms; `reconcile()` is unconditional again |
| - | 24 | not started | NEW, split from 06: ArmAssembly needs a module-boundary decision first |

**Twelve resolved, one part-landed, eleven open** (19 tickets became 24). Resolving 02 and 03 unblocked 04.
Tickets 06 and 11 are each still a multi-hour refactor of a hot path and want the toy-run digest,
not a tail-end slice.

Four pieces were split out rather than folded in, so a resolved status never hides outstanding
work: 21 (the builders take loose dicts, blocked on 04's oracle re-freeze), 22 (splitting
`gain.py`, which carries a trap), 23 (the seed -- split, then done the same day once 03 made it
cheap), and 20 itself, which came out of 02.

### Why ticket 11 has no cheap first step

Its most valuable piece looks like extending `test_written_columns_are_readable` from 1 writer
to 20. It cannot be done first: **4 of the 16 writers already defeat the existing regex**
(`_insert_work_events` parses 1 column of its real set; `free_index`, `shift_days` and
`site_receiving` parse none), and the only way to extend coverage without the declaration is a
BETTER REGEX OVER SOURCE -- which is the exact repair the ticket exists to stop. The ratchet
becomes possible once the columns are declared; it is not a way in.

### Gate baseline (fill in phase 0)

| # | Gate | Before | After | Owner if red |
|---|---|---|---|---|
| 1 | `context/verify_context.py` | green | green | |
| 2 | `context/arch/verify_architecture.py` | green | green | re-synced for the new test file |
| 3 | `context/arch/verify_site.py --fast` | green | green | rebuilt, once was enough |
| 4 | `runschema.contract --check` | green | green | |
| 5 | `runschema.preflight --check` | green | green | |
| 6 | `Schema.profile_tree --check` | RED | RED | drift 04 -- identical message both sides, NOT ours |
| 7 | `context/memory/verify_memory.py` | green | green | 113 memories |
| 8 | `path_guard.py --scan` | green | green | |
| 9 | `docref_guard.py --scan` | green | green | |
| 10 | calltree + digest surface (~20 s) | 62 pass / 11 s | 62 pass / 11 s | |
