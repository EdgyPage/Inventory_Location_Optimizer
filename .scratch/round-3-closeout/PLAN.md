# Round 3 closeout — the plan

Label: wayfinder:plan
Opened: 2026-09-16
Tracker: `map.md` beside this file holds Destination / Decisions / Fog. This file holds the
ordered work, what each step must produce before it counts as done, and **every run it asks for**.

**Nothing is running.** No run in this plan starts without a go-ahead. §"The runs" below is the
whole list, with costs, so it can be approved or cut item by item.

---

## What this plan is for

Round 3 landed its two named goals — configuration is centralized, and the superlinear sites are
measured rather than guessed. What remains is a short list of things I owe: one verification I
started and have not finished, one attribution the instrument could not settle, one candidate still
live, and a set of failures that are not ours and are recorded nowhere but a conversation.

The organizing decision: **nothing here is a refactor until a measurement asks for one.** Two of
this round's three biggest candidates were closed by reading numbers that already existed, and the
third turned out to be invisible to the instrument entirely. So every phase below is allowed to end
in "closed with a reason", and the two that are most likely to are marked.

---

## Status

Landed on `develop` this session, all verified by the tenth gate (~20 s, 34 tests) and the two
path/docref guards:

| commit | what |
|---|---|
| `5330f1ad` | derived layer resynced; 127.7 GiB of this session's run trees removed; 3 memories written + mirrored |
| `d7f3979a` | `delta_lift_idxs` refuted; `_trend()` added to the instrument |
| `93e14c35` | per-arm totals kept and fitted; `--config cluster_map` / `cmin` cells added |
| `7187e6f2` | the cluster-family lead recorded; both Inbound quadratics closed |
| `02a50c7e` | this plan |
| `9cbaf243` | the pyyaml-skip count, whose first correction was also wrong |

Everything above was **zero-CPU** — archived artifacts, source reading, and tests that run in
seconds. The measurements this plan still needs are the ones below.

---

## The runs

The complete list. Nothing else in this plan touches the machine for more than a few seconds.

| # | Run | Cost | Contends? | Answers |
|---|---|---|---|---|
| R1 | `pytest Tests/ -q -k "not gpu"` | **40–50 min**, 1 core | no | A1 — does the `conftest` fixture hold at suite scale |
| R2 | meso ladder, `--config cluster_map` | ~13 min, 1 core | **no** (call counts) | B1 — is `_ClusterMapPool` superlinear |
| R3 | meso ladder, `--config cmin` | ~13 min, 1 core | **no** (call counts) | B1 — is `_CoDemandPool` superlinear |
| R4 | deep ladder, `--workers 18` | **~1 h 45 m**, whole box | **YES — exclusive** | B **and** C2 — per-arm growth isolates the cluster family AND the travel-balanced arms |
| R5 | `pytest Tests/calltree/test_rank_cache_equivalence.py` | 7–13 min, 1 core | no | pre-merge byte-identity, only if B convicts |

**On contention.** R2 and R3 are safe beside R1 because the answer they produce is a **call count**,
which is exact and deterministic; only walls are contention-sensitive, and the meso tier's walls are
40× inflated by tracing and unquotable anyway. R4 is the opposite: it is a wall measurement across
18 workers and needs the box to itself — four samples of identical work once differed by +19.7,
+24.0, −0.6 and +1.4 s.

**Suggested order:** R1 ‖ (R2, R3) first — that is ~50 minutes of wall for three answers. Then read
B's result before deciding whether R4 is worth 1 h 45 m, because if B convicts, R4 should carry
*both* instruments and be run once rather than twice.

---

## Phase A — finish my own verification

**A1. Triage the full suite** (R1). Attribute **every** failure against the known baseline of 7
pre-existing architecture failures.

- Expected: the 6 contamination failures from the pre-fixture run are gone. That is the fixture's
  whole contract and the reason the run exists.
- The risk this run exists to find: a test that silently depended on a predecessor's CONFIG
  mutation. Such a failure is a **finding, not a regression** — but it is triaged one by one, and
  each gets a cause, not a guess.
- **Do not attribute by memory.** The count moves with tree state and arch-layer staleness (16 vs
  13 vs 5, all measured on this repo). If anything is ambiguous, diff against a control tree built
  with `git archive HEAD | tar -x` — `git worktree add` dies on this repo's generated filenames.
- `5330f1ad` resynced the derived layer, so the pre-existing set may have *shrunk*. Confirm it
  rather than assuming 7.

**Done when** every failure has a named cause and the pre-existing set is confirmed.

---

## Phase B — convict or acquit the cluster family

*Most likely to end in "closed with a reason".*

The deep ladder's slowest arm at every rung is in one placement family, and its `total_s` diverges
(local k 0.98 → 1.61) while the 136-arm sum stays linear at k = 1.05. This is a **lead, not a
conviction**: a max over a migrating argmax is not any arm's growth curve, and four arms taking
turns being unlucky produces a similar picture.

**B1. Run the two new cells** (R2, R3). These are the first artifacts ever to trace
`_ClusterMapPool` and `_CoDemandPool` — every existing cell runs a travel-balanced arm, so the
plan's static candidates there have never been measured at all.

**B2. Read them in this order, and not out of it:**

1. Check the x-axis actually moved and intermediate counts differ — a ladder whose rungs exceed the
   fixture runs the same catalogue and prints three ratios with no warning.
2. Read the **local exponents**, not the fit. A converging ratio and a complexity class both fit
   r² > 0.99; that is what cost this round its #1 candidate.
3. For anything flagged, find the **denominator** and ask whether it has a ceiling the code
   guarantees. That single question closed `delta_lift_idxs`.
4. Only then price it: counted calls × measured per-call cost, against the rung's own wall.

**B3. Then one of two outcomes, both acceptable:**

- **Acquitted** — record it in `COMPLEXITY_ROUND_FINDINGS.md` §3 beside the others, with the
  denominator and the trigger that would re-open it. Phase B ends, and C becomes the only live work.
- **Convicted** — the static candidates finally have a measured denominator: `_CoDemandPool`'s
  scans (`Assignment_Functions.py:938, 958, 964, 968`) and `_ClusterMapPool`'s `list.remove`
  (`:2671`). Reuse, do not reinvent: `_TravelBalancedPool.take`'s `(score, rank)` heap is the proven
  template; `_closest_abs` and `_index`'s swap-remove already exist. Byte-identical, proven by the
  frozen-oracle suite — or a **named break** under the standing decision, declared and never
  discovered, which also triggers R5 and a two-run digest.

---

## Phase C — settle the `_aisle_best` attribution

After the refutations, `_TravelBalancedPool._aisle_best` is **the only offender in the meso table
whose local exponent rises** (1.62 → 1.77). Its `take` scan was fixed this round; the surviving half
is the run-boundary rebuild, priced at 2.66 % of a rung, whose `R × A` decomposition the deep ladder
explicitly refused to corroborate (§2.3 — *"do not quote the 74 %"*).

**C1 — CORRECTED 2026-09-16, before R4 was run.** The plan originally said "add a `_FLOW_COUNTS`
entry". **That does not work**, and finding out after R4 would have wasted the run:

- `_FLOW_COUNTS` is resolved by `_flows(tree, flat)` against a **traced** call tree. The deep tier
  never traces — it shells out to `run_simulation` and parses what comes back, and its rungs carry
  `'counts': {}` literally.
- The deep tier's numbers come from `runtime_metrics.load_rows`, and that table is **all seconds and
  geometry**: `total_s`, the seven section columns, `n_bins`, `n_aisles`, `peak_rss_mib`. There is
  not one call count in the DDL.
- So giving the deep tier a count means a new `INTEGER` column in the `runtime` DDL — which moves
  the schema id, rides `--sync` before and `--accept` after, and needs a per-vintage
  `dataset.override` omitting it or every archived run falls back to a dataclass default and reads
  **0 rather than NULL**. That is the exact shape of the `free_bins` defect. It is a `schema-
  maintainer` change, not a line in a table.

**C2 — the route that needs no schema change, and it is already built.** `_aisle_best` lives in
`_TravelBalancedPool`, which backs exactly the `rank_labor` and `rank_cartlabor` arms
(`Assignment_Functions.py:1903, 1949` both return `_build_travel_balanced_pool_fn`). R4 now emits
**per-arm growth with a trend** (`93e14c35`), so it answers the question directly:

- if the `rank_labor` / `rank_cartlabor` arms are flat at deep scale while the cluster arms
  diverge, `_aisle_best` closes at 2.66 % and §2.3's retraction becomes permanent;
- if they diverge too, the rebuild is real at scale and C3 is worth designing — and the schema
  column becomes worth its cost, because then there is something to count.

**One deep ladder answers B and C both**, through the same per-arm instrument. That is the whole
reason R4 is run once, after B, rather than twice.

**C3**, only if C2 convicts: the argmin of `load[aid] + fq·cost(var)` is a lower-hull / kinetic-heap
problem. **Do not pre-commit to a design.** Find one, or close it with a stated reason and the scale
at which it becomes real.

**Ordering dependency:** if B convicts, R4 should carry both instruments. One deep ladder, two
answers — otherwise it is 1 h 45 m twice.

---

## Phase D — hand over what is not ours  *(zero-CPU; blocked only on A1's output)*

**D1. The pre-existing architecture failures.** They reproduce identically on a `git archive`
control tree, so they are source drift, not this round's doing — and they live only in a
conversation. Write them up with, for each: the failing assertion, the control-tree evidence, and
the specific drift. Known members: `add_from_bin` acquiring a caller in `Inbound/trailer.py`, a
missing `build_shared_assets → plan_warehouse` backbone edge, the profile-tree and
schema-compatibility gates. Confirm the set against A1 first.

**D2. The commensurability finding, which has no ticket.** The deep tier's `Σtotal_s / workers`
against the measured wall runs 0.26 → 0.52 across the ladder: **even at the top rung, half the
deep-tier wall is not per-arm work.** Every deep exponent is computed on the half that is. That is
either pool scheduling or a phase nobody has named, and it is the largest unexplained quantity in
the artifact.

**D3.** ✅ Done in `9cbaf243` — the `CLAUDE.md` pyyaml-skip count, whose first correction was also
wrong (it named the one file that cannot vanish).

---

## Phase E — make the round's guarantees permanent

**E1.** The tenth gate covers the calltree smoke and anchor files plus `test_digest_surface.py`
(~20 s, 34 tests). Confirm it still passes after B and C land, and that
`test_rank_cache_equivalence.py` stays *out* of it — 7–13 minutes is a pre-merge cost, and
shrinking it was explicitly refused.

**E2.** Every function fixed in B or C gets a complexity guard in the
`test_admit_held_is_linear.py` family, with all four parts: a **call-count** bound across a 10–100×
span (never a wall clock), an **equivalence** check against a genuinely different reference, a
**non-vacuity** check, and an **invariant** check. The non-vacuity part is not optional — every
other assertion in such a file is a bound satisfied by doing *less* work, so an undercounting
counter passes all of them.

---

## Verification

There is **no pytest config file and no pytest in CI**; every gate is hand-run, so each phase names
the ones it needs.

Per change, cheapest first:

```bash
python -m pytest Tests/unit -q
```

The frozen-oracle equivalence suite for the touched family — exact float `==` by design, ~4 s:

```bash
python -m pytest Tests/unit/test_co_demand_pool_equivalence.py Tests/unit/test_ranked_assign_pool_equivalence.py Tests/unit/test_travel_balanced_equivalence.py -q
```

Then the drift gates the change type demands (`CLAUDE.md` §1) — at minimum
`context/arch/verify_architecture.py` for any new import edge, and `verify_context.py` plus
`python -m Optimization.runschema.contract --check` for any `SHAPE_SOURCES` edit.

The two ladder commands, exactly as R2/R3 and R4:

```bash
python Tests/calltree/calltree_growth.py --ladder meso --knob skus --config cluster_map
python Tests/calltree/calltree_growth.py --ladder meso --knob skus --config cmin
python Tests/calltree/calltree_growth.py --ladder deep --workers 18 --profile-run perf_mixed_40k__mixed_realistic_bell_lt0
```

Count deltas must be **exactly** as intended, and the re-fitted exponent must move — a refactor that
leaves k unchanged has not fixed the complexity, whatever the wall says.

For anything that moves a real run's numbers — every named break, mandatorily:

```bash
python Tests/bench/run_digest.py --self-test
python Tests/bench/run_digest.py <baseline_run_root> <candidate_run_root>
```

---

## Traps this plan must not walk into

1. **Never quote a traced second.** Tracing costs ~40×; untraced section walls are the truth, and
   the deep tier's `t_*` are *mean seconds per batch per arm*, never a share of the wall.
2. **Read the local exponents before the fit.** The round's #1 offender was a bounded ratio
   saturating toward a ceiling the code guarantees, and it fitted r² = 0.994 for three weeks.
3. **A count is not a claim.** Divide by a denominator, and never derive a per-iteration cost by
   dividing the total you are trying to explain.
4. **Check WHICH ARM a passing test exercises.** The ranked-assign oracle passed 42/42 on a heap it
   never ran; the meso ladder's whole offender table describes one arm out of 136.
5. **A ladder saturates in silence.** Rungs above the fixture's catalogue run the same catalogue and
   print different ratios with no warning. Check intermediate counts differ, not just inputs.
6. **The RUN wall needs a quiet host.** Call *counts* are exact and immune; walls are not. This is
   the whole basis for running R2/R3 beside R1 and R4 alone.
7. **Run the full suite, not three of its tiers.** `Tests/architecture` was silent for this entire
   round because I ran `unit`, `integration` and `e2e` and reported the total as if it cleared the
   changes.
8. **`sed -i` destroys CRLF** on this repo's mixed-newline working copy, and a heredoc eats escapes.
   Use the newline-preserving patcher, or the `Edit` tool.
9. **SECTION_MAP updates ride hot-path renames** — `test_calltree_anchors.py` names the exact entry
   to fix when a refactor moves a symbol.
10. **A deep ladder leaves ~128 GiB of run trees.** They are not committed (`CLAUDE.md` §2) but they
    are not free either; R4's cleanup is part of R4, not a later chore.

---

## Ordering, and why

`A ‖ B → C → D → E`, with C gated on B's result and D gated on A's.

D is last among the work but is **not optional** — leaving the pre-existing failures in a
conversation is how they stay invisible for another three weeks, which is precisely how they got
here. If time runs short, D1 and D2 are the two that must still be written.
