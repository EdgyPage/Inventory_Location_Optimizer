# S01 -- an exact replay of `plan_order` for merge bundles (registered 2026-09-24)

## Question

`plan_order` (`Inbound/gain.py`) prices T(T+1) virtual placements per plan.  Under the
merge rung (`tmin`/`tmax`, and every pool family priced with `force_merge`), each placement
rebuilds its tiers' availability by filtering the pre-sorted tier against an exclusion SET
(`_avail`: O(|tier|) per tier touched), and each defer side builds `B - hole_t` (O(|B|)).
Can the plan be replayed with one integer frontier per tier instead, exactly?

## Hypothesis

Two sorted lists sharing an index is already what the merge rung does per placement:
units in -(f x labor_cost) order take the tier's bins in D order.  What is NOT exploited is
that the exclusion sets have a fixed shape.

## Derivation

Let `S_k` be `_tier_sorted(k)` (stable, fixed per drain).

1. **`taken ∩ S_k` is a prefix `S_k[0:F_k]`.**  Induction: initially empty.  A now-side
   placement filters `S_k` by `taken` (a prefix) and walks a cursor from the first
   survivor, so its takes in tier k are `S_k[F_k : F_k + n_tk]`, contiguous.  A commit
   adds exactly those, so the prefix property survives the commit.
2. **The round's sweep is nested prefixes.**  Every candidate's now-takes in tier k are
   `S_k[F_k : F_k + n_tk]`, so `K ∩ S_k = S_k[F_k : F_k + N_k]` with `N_k = max_t n_tk`,
   and a position `F_k + j` is counted by `#{t : n_tk > j}` candidates.
3. **The hole is a suffix of the sweep.**  `count == 1` exactly at `j >= n(2)_k` (the
   second-largest `n_tk`, counting multiplicity) and only when one candidate holds the
   maximum; that candidate's hole in tier k is `S_k[F_k + n(2)_k : F_k + N_k]`, and every
   other candidate's is empty.
4. **So every defer view is a suffix.**  `S_k` filtered by `B - hole_t` is
   `S_k[L_tk:]` with `L_tk = F_k + n(2)_k` if t is tier k's unique maximum, else
   `F_k + N_k`.  With `predicted`, the view is `heapq.merge(S_k[L_tk:], P_k, key=D)`,
   and `heapq.merge` breaks equal keys toward the FIRST iterable in both directions --
   a two-pointer walk with the same rule.

The replay prices each placement by walking these suffixes with the SAME `_place_merge`
(unit order, pricing, unseated accounting), fed offsets instead of filtered copies.

## Prediction (committed before measuring)

| # | quantity | predicted | falsified by |
|---|---|---|---|
| P1 | order, every trace record `[seq, now, defer, gain]`, final `ev.taken`, `ev.unseated` vs `plan_order(force_merge=True)` / the merge rung | **bit-identical** on the test_gain_plan scenes and >= 500 randomised scenes (ties, shared spill chains, forced prefix, predicted on/off, tmin/tmax, unseated units) | any difference, however small |
| P2 | plan wall at campaign shape (T = 17, ~4,000-bin tiers, ~160 units/trailer) | **>= 20x** faster than the generic merge path | < 5x |
| P3 | scaling in yard depth T | generic ~T^2 x (tier size); replay ~T^2 x (units per trailer) with no tier-size term | replay wall grows with tier size at fixed T |

## Measurement (2026-09-24)

**P1 -- exactness.**  `Tests/unit/test_plan_order_merge_replay.py` (8 tests, 0.8 s):
600 randomised cases (150 scenes x minimize both ways x predicted on/off, forced prefix
on a third) return the same order, the same trace records `[seq, now, defer, gain]`, the
same final `taken` and the same `unseated` as `plan_order(..., replay=False)`, bit for
bit.  The scenes are checked to contain every shape the derivation turns on (unique and
shared longest reach, unseated units, a forced prefix, spill into the next tier, and
equal-D ties across the now and predicted tiers at DIFFERENT coordinates -- the only tie
that prices differently); each shape occurs >= 10 times.  Three planted errors are each
caught: no hole for the unique longest reach, an off-by-one frontier, and the predicted
tier winning `heapq.merge` ties.  The existing gain suites pass unchanged
(`test_gain_plan.py`, `test_plan_trace.py`, `test_gain_cow_equivalence.py`: 103 tests).

**Run level.**  `_toy_merge` (a new research spec: fifo / tmin / tmax under fifo,
gmyopic, gforecast and ggated, one door) on the pre-change commit plus the spec vs the
change: `run_digest.py` **IDENTICAL on the comparable surface, 48 arms**, and the plan
traces' exact orders identical.  But every one of its 108 plans had T = 1, so that run
proves the path is wired and inert, not that deep plans agree.  A second pair at the
calltree yard ladder's deepest settings (one door, one-person team, zero lead, 2,400
SKUs, 10 batches) was also IDENTICAL, and also T = 1 throughout: under
`PHASE2_RUN_DEFAULTS` the derived crews keep the yard empty.

**Deep plans, shadowed** (`assets/s01_shadow.py`).  The calltree ladder's deepest rung
run in process (receiving whistle 80 s, two receivers) with `plan_order` wrapped so that
every plan the simulation asks for is priced on BOTH paths and must agree in order and
trace; the run itself proceeds on the replay:

| arm / policy | plans | T histogram (T: count) | set / replay wall |
|---|---|---|---|
| tmin / gain_forecast | 18, all equal | 1:9, 3:2, 4:3, 8, 12, 25, 37 | 5.9x |
| tmin / gain_myopic | 18, all equal | same | 5.5x |
| tmin / gain_gated | 18, all equal | same | 5.5x |
| tmax / gain_forecast | 18, all equal | 1:9, 3:3, 4:2, 7, 11, 24, 35 | 8.0x |

Plus a unit test that a POOL family priced with `force_merge` (the plan-trace probe's
reduction) replays bit for bit, and one priced by its own pool never replays.  The meso
tiers are small (the speedup is 5-8x there, against 15x at campaign-shaped tiers).

**P2 / P3 -- speed** (`assets/s01_replay_bench.py`, results/s01_replay_bench.json; 160
units per trailer, six BinKeys, predicted on; median of 3):

| T | 1,000-bin tiers | 4,000 | 16,000 |
|---|---|---|---|
| 4 | 3.8x | 7.6x | 11.5x |
| 8 | 4.3x | 12.3x | 27.1x |
| 17 | 4.4x | **15.0x** | 50.3x |
| 25 | 4.4x | 15.7x | 58.5x |

P2 (>= 20x at T = 17 on ~4,000-bin tiers) is **missed at 15x** (not falsified: > 5x).
P3 holds: the replay's wall is nearly flat in tier size (0.060 -> 0.064 -> 0.081 s at
T = 17) where the set path's grows linearly (0.26 -> 0.95 -> 4.06 s).

## Residual and diagnosis

What is left of the replay's wall is the placements themselves: at T = 17, 306
placements x 160 units = 49k `_cost_at` calls (0.10 s of 0.29 s under cProfile), plus
`place_load_at`'s grouping (51k `binkey_of`) and `_place_merge`'s per-call unit sort
(1,836 calls).  None of it depends on the tier, which is why the speedup grows with tier
size and saturates in T.

## Revision (next, optional)

The grouping and the unit sort are per-TRAILER facts recomputed at every placement;
caching them per load removes ~25% more.  Deferred: the evaluator stops being the
bottleneck at 15x, and the bigger question moved to which RULE to price (S02-S04).
