# Round 3 closeout — the plan

Label: wayfinder:plan
Opened: 2026-09-16
Tracker: `map.md` beside this file holds Destination / Decisions / Fog. This file holds the
ordered work and what each step has to produce before it counts as done.

---

## What this plan is for

Round 3 landed its two named goals — configuration is centralized, and the superlinear sites are
measured rather than guessed. What it also produced is a **short list of things I owe**: one
verification still running, one attribution the instrument could not settle, one candidate that is
still live, and a set of failures that are not ours and are currently recorded nowhere but a
conversation.

The organizing decision: **nothing here is a refactor until a measurement asks for one.** Two of
this round's three biggest candidates were closed by reading numbers that already existed, and the
third turned out to be invisible to the instrument entirely. That is the ratio this plan expects to
continue, so every phase below is allowed to end in "closed with a reason".

---

## Status at the top of this plan

Landed this session, on `develop`:

| commit | what |
|---|---|
| `5330f1ad` | derived layer resynced, 127.7 GiB of this session's run trees removed, 3 memories written + mirrored |
| `d7f3979a` | `delta_lift_idxs` refuted; `_trend()` added to the instrument |
| `93e14c35` | per-arm totals kept and fitted; `--config cluster_map` / `cmin` cells added |
| `7187e6f2` | the cluster-family lead, and both Inbound quadratics closed |

In flight: the full routine suite (first run with the `conftest` fixture), and the two new meso
ladders.

---

## Phase A — finish my own verification  *(in flight, blocks nothing else)*

**A1. Triage the full suite.** `python -m pytest Tests/ -q -k "not gpu"`, running since 16:07.
Attribute **every** failure against the known baseline of 7 pre-existing architecture failures.

- Expected: the 6 contamination failures from the pre-fixture run are gone. That is the fixture's
  whole contract and the reason the run exists.
- A NEW failure is the risk this run exists to find: a test that silently depended on a
  predecessor's CONFIG mutation. Such a failure is a **finding, not a regression** — but it is
  triaged one by one, and each gets a cause, not a guess.
- **Do not attribute by memory.** The count moves with tree state and arch-layer staleness (16 vs
  13 vs 5, all measured on this repo). If anything is ambiguous, diff against a control tree built
  with `git archive HEAD | tar -x` — `git worktree add` dies on this repo's generated filenames.

**Done when** every failure has a named cause and the pre-existing set is confirmed unchanged.

---

## Phase B — convict or acquit the cluster family  *(the one open lead)*

The deep ladder's slowest arm at every rung is in one placement family, and its `total_s` diverges
(local k 0.98 → 1.61) while the 136-arm sum stays linear at k = 1.05. This is stated as a lead
because **a max over a migrating argmax is not any arm's growth curve** — four arms taking turns
being unlucky produces a similar picture.

**B1. Run the two new cells** (in flight): `--config cluster_map` and `--config cmin`, meso, knob
`skus`. Under a minute of simulation each. These are the first artifacts ever to trace
`_ClusterMapPool` and `_CoDemandPool`.

**B2. Read them with the new discipline, in this order:**

1. Check the x-axis actually moved and intermediate counts differ — a ladder whose rungs exceed the
   fixture runs the same catalogue and prints three ratios with no warning.
2. Read the **local exponents**, not the fit. This is the whole lesson of `d7f3979a`: a converging
   ratio and a complexity class both fit r² > 0.99.
3. For anything flagged, find the **denominator** and ask whether it has a ceiling the code
   guarantees. That single question closed the round's #1 candidate.
4. Only then price it: counted calls × measured per-call cost, against the rung's own wall.

**B3. Then one of two outcomes, and both are acceptable:**

- **Acquitted** — record it in `COMPLEXITY_ROUND_FINDINGS.md` §3 beside the others, with the
  denominator and the trigger that would re-open it. Phase B ends.
- **Convicted** — the plan's static candidates finally have a measured denominator:
  `_CoDemandPool`'s three per-take scans (`Assignment_Functions.py:938, 958, 964, 968`) and
  `_ClusterMapPool`'s `list.remove` (`:2671`). Reuse, do not reinvent: `_TravelBalancedPool.take`'s
  `(score, rank)` heap is the proven template, `_closest_abs` and `_index`'s swap-remove already
  exist. Byte-identical, proven by the frozen-oracle equivalence suite, or a **named break** under
  the standing decision — declared, never discovered.

**Done when** the family is either refactored with a re-measured exponent, or closed with a
denominator and a trigger written down.

---

## Phase C — settle the `_aisle_best` attribution

After the refutations, `_TravelBalancedPool._aisle_best` is **the only offender in the whole meso
table whose local exponent rises** (1.62 → 1.77). Its `take` scan was fixed this round; the
surviving half is the run-boundary rebuild, priced at 2.66 % of a rung, whose `R × A` decomposition
the deep ladder explicitly refused to corroborate (§2.3 — *"do not quote the 74 %"*).

**C1.** The deep tier carries **no function counts at all**, so the one instrument that reaches
deep scale cannot see the rebuild. Add a `_FLOW_COUNTS` entry for it — a count, not a wall, so it
survives the 40× tracing distortion that makes deep-tier seconds unquotable.

**C2.** Re-run the deep ladder and read the rebuild's count per rung against placements. If
`R × A` holds at deep scale, the attribution stands and the structural fix is worth designing; if
it does not, §2.3's retraction becomes permanent and the candidate closes at 2.66 %.

**C3.** The structural fix, only if C2 convicts: the argmin of `load[aid] + fq·cost(var)` is a
lower-hull / kinetic-heap problem. **Do not pre-commit to a design.** Find one, or close it with a
stated reason and the scale at which it becomes real.

**Note the ordering dependency:** if Phase B convicts the cluster family, *that* is the larger
finding and C2's deep re-run should carry both instruments, not one. One deep ladder, two answers.

---

## Phase D — hand over what is not ours

**D1. The 7 pre-existing architecture failures.** They reproduce identically on a `git archive`
control tree, so they are source drift, not this round's doing — and they are currently recorded
only in a conversation. Write them up with, for each: the failing assertion, the control-tree
evidence, and the specific drift. Known members: `add_from_bin` acquiring a caller in
`Inbound/trailer.py`, a missing `build_shared_assets → plan_warehouse` backbone edge, the
profile-tree and schema-compatibility gates.

Confirm the set against Phase A's output first — `5330f1ad` resynced the derived layer and may
have resolved some of them.

**D2. The commensurability finding, which has no ticket.** The deep tier's
`Σtotal_s / workers` against the measured wall runs 0.26 → 0.52 across the ladder: **even at the
top rung, half the deep-tier wall is not per-arm work.** Every deep exponent is computed on the
half that is. That is either pool scheduling or a phase nobody has named, and it is the largest
unexplained quantity in the artifact.

**D3. Correct `CLAUDE.md` §3** while in the area: it says "6 of the 13 `Tests/architecture/*`"
`importorskip` without pyyaml. It is now 8 of 31.

---

## Phase E — make the round's guarantees permanent

**E1.** The tenth gate now covers the calltree smoke and anchor files plus
`test_digest_surface.py` (~16 s, 48 tests). Confirm it still passes after Phase B and C land, and
that `test_rank_cache_equivalence.py` stays *out* of it — 7–13 minutes is a pre-merge cost, and
shrinking it was explicitly refused.

**E2.** Every function fixed in Phase B or C gets a complexity guard in the
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

Per complexity candidate:

```bash
python Tests/calltree/calltree_growth.py --ladder meso --knob skus --config cluster_map
python Tests/calltree/calltree_growth.py --ladder deep --workers 18 --profile-run <name>
```

Count deltas must be **exactly** as intended, and the re-fitted exponent must move — a refactor
that leaves k unchanged has not fixed the complexity, whatever the wall says.

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
5. **A ladder saturates in silence.** Rungs above the fixture's catalogue run the same catalogue
   and print different ratios with no warning. Check intermediate counts differ, not just inputs.
6. **The RUN wall needs a quiet host** — four samples of identical work once differed by +19.7,
   +24.0, −0.6 and +1.4 s. Call *counts* are exact and immune; walls are not.
7. **Run the full suite, not three of its tiers.** `Tests/architecture` was silent for this entire
   round because I ran `unit`, `integration` and `e2e` and reported the total as if it cleared the
   changes.
8. **`sed -i` destroys CRLF** on this repo's mixed-newline working copy, and a heredoc eats
   escapes. Use the newline-preserving patcher, or the `Edit` tool.
9. **SECTION_MAP updates ride hot-path renames** — `test_calltree_anchors.py` names the exact entry
   to fix when a refactor moves a symbol.

---

## Ordering, and why

`A → B → C → D → E`, with one deliberate overlap: Phase B's ladders are call-count measurements,
and **call counts are exact and contention-immune**, so they may run beside Phase A's suite. Walls
may not.

D is last among the work but is **not optional** — leaving the pre-existing failures in a
conversation is how they stay invisible for another three weeks, which is precisely how they got
here. If time runs short, D1 and D2 are the two that must still be written.
