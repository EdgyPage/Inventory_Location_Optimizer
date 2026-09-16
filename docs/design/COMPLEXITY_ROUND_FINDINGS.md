# Complexity round — what the instrument was wrong about, and what it found once it was fixed

Successor to `INBOUND_PERF_FINDINGS.md`, and it inherits that document's most valuable property:
**it retracts its own wrong findings in place.** Most of the corrections below are to claims this
round itself made before measuring. Read them first if you are here to cite a number.

(No count is given, deliberately. This round corrected a CLAUDE.md line that had said "6 of the
13" while the directory had grown to 31 — a tally in prose rots faster than the thing it
counts, and a document about not trusting numbers should not open with one it cannot maintain.)

Reproduce with:

```bash
python -m pytest Tests/calltree -q                                  # the instrument is honest
python Tests/calltree/calltree_growth.py --ladder meso --knob skus
python Tests/calltree/calltree_growth.py --ladder meso --knob skus --config split_staging4
python -m pytest Tests/unit/test_placement_selection_is_not_a_scan.py -q
```

---

## 0. The instrument was wrong three ways, and every one flattered a false finding

Nothing below could be trusted until these were fixed, and none of them would have been found by
reading the code — all three were found by reading the tool's own output.

### 0.1 The deep ladder's ONLY offender was timer noise

`t_task`, k = 3.916 at r² = 0.946 — a textbook super-linear exponent at a convincing fit, at the
top of the table. Its walls:

```
[4.9e-05, 3.9e-04, 0.0356, 0.0927] seconds
```

**The fit rests on fifty microseconds**, and the whole section is worth 93 ms at the top rung. The
existing gate (`max(ys) < 0.01`) asks whether a section ever got BIG; what poisons a log-log fit
is the SMALLEST positive point, because that is the end of the lever arm. `MIN_WALL_S` now sends
such a fit to `report['suppressed']` **with its reason** rather than dropping it — "too small to
fit here" is itself a finding.

> **If you re-derive this, use the LADDER's values, not the REPORT's.** The rendered `walls` field
> rounds the first rung to `0.0`, and a fit on the rounded series gives k = 3.928 at r² = 0.877,
> which the r² gate would drop for an entirely different reason. The first version of the test for
> this made exactly that mistake.

### 0.2 Offenders ranked by a number that means five different things

`sort(key=-exponent)` put section seconds, call counts, per-placement ratios, arm totals and knees
in one order. A knee's "exponent" is a local step between two rungs; a section's is a fitted wall.
That is the lesson the effort which BUILT this tool recorded — *"element counts must be weighted by
COST CLASS before ranking"* — applied to the tool that taught it.

**RETRACTED, by this round, about this round's own fix:** the re-ranking was described as
surfacing buried findings. Measured, it moves **one entry** on the meso ladder (a 5.4 M-call
accessor, rank 9 → 5). Re-fitting the archived LADDER with today's code yields 10 offenders where
the archived REPORT listed 5, and it is tempting to bank that gap as the new ranking's work. It is
not — it is fitter drift since 2026-08-26.

### 0.3 The ladder was fitting exponents on a sampler production retired in September

`calltree_scenarios` built its `BatchConfig` with no `sampler`, taking the dataclass default
`'v1'` — the O(k·N) cumsum draw — while `settings.SAMPLER` has been `'v3'`, the segment tree,
since 2026-09-12. `Batch.__init__` is `SECTION_MAP`'s anchor for `t_sample`, so **every `t_sample`
exponent in `out/archive/` describes code no production run executes.**

Measured directly, k = 0.15·N, five rungs 500–8,000:

| sampler | k | r² |
|---|---|---|
| v1 (the fixture's) | **1.477** | 0.993 |
| v3 (production's) | **0.822** | 0.901 |

9.6× apart at N = 8,000, against the archived ladder's `t_sample` k = 1.522. The attribution
closes. After the fix, `t_sample` fits **1.11 / 1.09** and has left the offender table — a
prediction recorded before the re-run.

**And the switch moved no measurement**, which was not expected. At 2,000 SKUs over ten seeds v1
and v3 draw byte-identical batches — same SKUs, same quantities, same draw ORDER. Both implement
"the first index whose cumulative weight passes the draw" and consume one uniform per draw; they
part company only where v1's float accumulation does, which is the production catalogue's ~1e26
weight range, not a benign fixture.

> **CORRECTION to `Warehouse/picking/Workload_Builder.py`:** its comment says v3 "moves every
> batch sequence". That is true where it was written to be true — a production run — and **not at
> fixture scale**. It should not be read as "any sampler switch is a new baseline".

### 0.4 A warning that cried wolf on every rung

The per-rung output printed, two lines apart, on the same rung:

```
flows (traced, cumulative): ... held_appends=40,720 held_retry_touches=50,912 ...
flows: ALL ZERO -- the put-away/receiving path did not execute under cfg=split_staging4.
                   Use --config split_staging4 to exercise it.
```

It was the `else` of the per-ENTRY-CALL branch — an inbound-only quantity — so on every
non-inbound config it fired regardless of the flows, and advised switching to the config already
in use. The same nesting hid the per-placement RATIOS, which were computed and fitted but never
printed per rung.

`Tests/calltree/README.md` instructs the reader that `flows: ALL ZERO` **means "not measured"**,
and records a run whose held path executed thirteen million times reporting `held: 0` with that
zero read as "the path never ran". A warning that cries wolf on every rung trains the reader to
skip the one line that exists to stop them trusting a zero.

---

## 1. The HEAD offender table

Run 2026-09-16 on `608fbfc9`, sequentially on a quiet host. **No archived artifact is quotable**:
all 38 predate `bd29d2eb` (the `take` heap), the one `yard` artifact's x-axis spans 4 % with
duplicate values, and every `t_sample` figure is the retired sampler.

**Nothing is a section-wall offender.** The highest is `t_reord` at k = 1.48 against a 1.50 flag —
close, and it is the section placement happens in, so it corroborates rather than convicts. Every
conviction is a CALL COUNT, which is the sharper early-warning instrument and is not itself a cost.

| offender | k (none) | k (split_staging4) | calls at top rung |
|---|---|---|---|
| `AffinityStore.delta_lift_idxs` | 1.59 | 1.43 | 157,981 |
| `_TravelBalancedPool._aisle_best` / `._score_of` | 1.54 | 1.57 | 427,497 |
| `delta_lift_idxs.<locals>.<genexpr>` | 1.52 | 1.49 | 1,581,454 |
| `cost_model:per_pick` | — | **1.31** | 542,781 |
| `sum_lift.<locals>.<listcomp>` | 1.32 | — | 29,715 |

### It is ONE mechanism, not five

A raw count cannot separate "more units" from "more work per unit". Dividing by the run's own
placements:

| | per placement | k |
|---|---|---|
| `_aisle_best` | 0.366 → 0.474 → 0.716 → **1.205** | **0.573** |
| `delta_lift_idxs` | 0.157 → 0.264 → 0.366 → **0.445** | **0.502** |
| `per_pick` (ss4) | 5.298 → 6.775 → **9.409** | **0.414** |

Work per unit IS growing, and all three grow together because they are the same thing: **every
family re-scores EVERY AISLE at every SKU-run boundary, so per-unit cost tracks the aisle count.**
`_aisle_best` and `_score_of` are called 1:1; `per_pick` sits inside `_aisle_best`, once per
(aisle, height bracket), which is why its per-placement figure is ~8× theirs.

### RETRACTED: "the `take` heap fixed `_aisle_best`"

Reported during this round's exploration and wrong. `_aisle_best` is convicted on HEAD under both
configs and its per-placement cost more than TRIPLES across the ladder. `bd29d2eb` removed the
O(A) **scan** from `take`'s selection; it never touched the **run-boundary rebuild**, which calls
`_aisle_best` once per aisle every time the SKU changes.

---

## 2. The near-quadratic, decomposed — and priced

### 2.1 The subtraction that would have misattributed it

`_aisle_best` has two call sites with different complexity, and the ladder counts only their
total. The obvious split — total minus placements — gives:

```
-29,492   -45,987   -49,804   +72,727
```

**Negative at three of four rungs.** "One refresh per placement" is false (the scenario's
`placements` counts every placement path, not only this pool's takes), so the split is not
recoverable from the columns the ladder prints. This is the trap `INBOUND_PERF_FINDINGS.md` names
twice and then falls into: *"the 0.630 µs was obtained by dividing the very total it then claimed
to explain."*

### 2.2 Measured instead

A probe wrapping `take` (reading `self._run_sku` against the unit's SKU — the same condition the
rebuild branch tests). Nothing under `Warehouse/` edited.

| skus | boundaries R | rebuild | refresh | A (live aisles) |
|---|---|---|---|---|
| 600 | 1,767 | 8,245 | 7,288 | 4.7 |
| 1,200 | 3,877 | 34,227 | 15,426 | 8.8 |
| 2,400 | 7,524 | **116,712** | 29,138 | 15.5 |

| series | k vs skus |
|---|---|
| **rebuild** | **1.912** |
| refresh | **1.000** |
| boundaries R | 1.045 |
| aisles A | 0.867 |

**1.045 + 0.867 = 1.912** — the two factors sum to the measured product exponent to three
decimals. Two self-checks hold exactly: `R × A_mean = rebuild_calls` (116,712), and
`refresh == takes` at every rung. The rebuild is 80 % of the calls at 2,400 SKUs, up from 53 % at
600, because it is the only half that grows super-linearly.

### 2.3 And priced, because a count is not a cost

`_aisle_best` measured on its real shape: **0.434 µs**. Paired with the ladder's own 2,400 rung so
the count and the wall come from one run:

| | ms | share of the 3.78 s rung |
|---|---|---|
| all `_aisle_best` | 126 | 3.32 % |
| **the rebuild half** | **100** | **2.66 %** |

**The headline is therefore: k = 1.912 on a term worth 2.7 % today.** That is not yet a reason to
refactor anything.

It stays the top candidate because the SHARE grows: the rung wall fits k = 1.26, the rebuild
1.912, so the share rises as n^0.651 — ~12 % at 24 k SKUs, ~53 % at 240 k, ~74 % at campaign scale.

> **That projection is a PREDICTION, not a result.** It extrapolates a 4×-span fit out to 167×,
> which is the move that cost `inbound-performance` three retractions. The deep ladder tests it
> before any lazy-bound work is built.

### RETRACTED in part: the deep ladder corroborates the bend, not the attribution

The 8× deep ladder ran (10,000 → 80,000 SKUs, 136 arms per rung, 400k catalogue). `reord_s`
bends exactly as a growing super-linear term would — local exponents **0.88 → 1.13 → 1.31 →
1.38** — and the rise is **specific to that section**, while `save_s` falls and `sim_s`,
`build_s` stay flat. The headline fit of k = 1.12 understates the top end badly.

But fitting `reord_s = A·n + B·n^q` with `q` fixed at **1.912**, the exponent independently
measured for the rebuild, fits **worse than a plain power law**:

| model | worst rung | log residual |
|---|---|---|
| single power law, k = 1.12 | 8.6% | 0.0249 |
| **linear + rider at q = 1.912** | **16.9%** | **0.0354** |
| linear + rider at q = 2.60 (fitted) | 9.2% | 0.0098 |

`reord_s` carries the whole reorder phase, not just the pool's aisle selection. **The bend is
real; that it is the `R × A` rebuild is unsupported. Do not quote the 74%.** What would settle
it is the rebuild's own call count per deep rung — a `_FLOW_COUNTS` entry rather than an
inference from a section wall — which the deep tier does not have, because its sections come
from each run's log rather than a tracer.

Also measured: **commensurability runs 0.26 → 0.52 across the ladder.** Even at the top rung
half the wall is not per-arm work. A deep-tier wall read as "the cost of the simulation"
is roughly double.

---

## 2.5 The assignment families were invisible, and the one that showed up was the biggest

Every config ran `DEFAULT_STRATEGY`, a travel-balanced arm, so `_RankedAssignPool` — the pool
behind `tmin`, `tmax`, `rank_random` and `rank_popularity` — was structurally unreachable from
every rung. Two new ladder cells fixed that and found the round's largest exponent.

`--config ranked_popularity`, meso `skus` 500..8,000:

| skus | wall | selector-lambda calls | takes | **aisles scanned per take** |
|---|---|---|---|---|
| 500 | 0.36 s | 34,134 | 4,191 | **8.1** |
| 8,000 | 12.02 s | **8,819,328** | 76,514 | **115.3** |

**k = 1.963**, above the `R × A` rebuild's 1.912, and it needs no fitting to read: the scan width
IS the live aisle count, growing 14× across a 16× catalogue, while `take` itself is exactly linear
(k = 1.018). Priced at 0.52 s of a 12.02 s rung — **4.3%** — with the share rising as n^0.698
(~21% at 80 k SKUs, ~66% at campaign scale).

### A scan keyed on a C callable is INVISIBLE here

`ranked_tmin` runs the same pool with identical `take` counts and reports **no scan at all** —
because its key is `head_D.__getitem__`, a C method the tracer records as `kind='ext'` and
`_flat_counts` then skips. `tmin` is not cheaper; its scan is the same 115.3 aisles wide.

The exclusion has a good reason (C leaves would break `counts_fingerprint`'s determinism) and an
unstated consequence: **the count instrument systematically under-reports the cheapest-to-write
form of the most common superlinear shape in this codebase.** Not fixed — counting C leaves is the
worse trade. The durable mitigation is to fit the SCAN WIDTH (`lambda / takes`), which is immune
to what the key is written in.

### What landed, and what it cost to trust it

`take` now selects from a `(key, rank, aid)` heap. No lazy deletion, and — unlike
`_TravelBalancedPool` — **no run-boundary rebuild at all**, because neither key depends on the
SKU. `rank_random` keeps the scan deliberately.

| | key evaluations at 8,000 SKUs | k |
|---|---|---|
| before | 8,819,328 | 1.963 |
| after | **120,149** | **1.111** |

73× fewer; `take` unchanged at 76,514; placements identical at every rung. Predicted saving
0.51 s from two independently measured factors, observed wall 12.02 → 11.20 s. **The count is the
result; the wall is corroboration only.**

**The equivalence test was VACUOUS and passed 42/42 anyway.** It built `rank_popularity` with
`aisle_selector` — the scan production had just stopped using — so it green-lit a heap it never
executed. Same failure as `inbound-performance` ticket 05, except that one failed loudly because
a refactor moved the seam, and this one would not have failed at all. Both shapes are now arms, so
the agreement is three-way (`impl(scan) == pool(scan) == pool(heap)`), with the reference side
translating the key back into the scan so the oracle stays the RETIRED algorithm. Inverting the
tie-break fails 14 of 51.

---

## 3. Closed with a reason, not refactored

**The `per_pick` memo** that `INBOUND_PERF_FINDINGS.md` recommends. Priced before building, against
the count the ladder recorded:

| form | per 542,781 calls | share of the rung |
|---|---|---|
| `per_pick(...)` — today | 42.8 ms | — |
| `cache[m]` — **the suggested memo** | 15.5 ms | 0.72 % |
| `m * base` — hoist the invariant | 10.4 ms | **0.86 %** |

Under one percent either way, and **the suggested form is 1.5× worse than the alternative**: a
float-keyed dict pays hashing where a hoist pays one multiply, and what both actually remove is the
Python function call. Bit-identity was verified (`m * base` is IEEE byte-equal at qty = 1) and is
not the obstacle; the obstacle is that the hoist means NOT calling `per_pick`, and that primitive
exists to stop callers re-deriving the expression. Its share is scale-stable, so "run it bigger"
does not get there — the structural half of the same candidate removes these calls entirely.

**`AffinityStore.delta_lift_idxs` — the #1 offender in every archived ladder, and asymptotically
linear.** It led the HEAD table at k = 1.59 (r² = 0.994), led every one of the fourteen archived
`skus` ladders back to August, and was the plan's largest remaining candidate. It is not a
candidate at all.

Three facts, none of which needed a refactor to establish:

1. **The callers are not the ones the plan named.** The plan placed it "per (placement, aisle)".
   All six archived captures agree it has exactly ONE parent, `ReorderMixin._reclaim_empty_bins`
   (`inventory_reorder.py:426`) — the bin-teardown path, not placement. The placement-side call at
   `:240` never fires in any traced configuration.
2. **Per call, the cost is capped by construction.** The work is one pass over the SKU's CSR row
   (`Affinity_Store.py:387-389`); the `ci in member_idx_set` test is a hash probe, so the aisle's
   size does not enter. Row nnz is the SKU's own `top_k = 10` plus the reverse-direction edges of
   mates that chose it, bounded by `cluster_size - 1 = 79` and ~20 in expectation
   (`generate_affinity.py:89-92, 340-345`). Both constants are fixed — **row nnz does not grow with
   the catalogue.** And the ladder measures this directly, because the row scan is a generator
   with its own counter — frames per parent call, across a 16× catalogue increase:

   | max_skus | 500 | 1,000 | 2,000 | 4,000 | 8,000 |
   |---|---|---|---|---|---|
   | elements per call | 11.9 | 13.9 | 13.8 | 12.4 | **10.0** |

   Flat, and if anything *declining*. The per-call cost was never the growing term, and no code
   argument was needed to see it — the number sat in the same artifact as the exponent.
3. **The call count is a bounded ratio, caught mid-saturation.** `_reclaim_empty_bins` calls
   `_index_add` once per reclaimed bin unconditionally and `delta_lift_idxs` only when that bin was
   the SKU's LAST in its aisle. So calls ≤ reclaimed bins, always. `_index_add` grows k = 0.99 —
   linear. The ratio between them:

| max_skus | `delta_lift_idxs` | `_index_add` | ratio | local k of the calls | local k of the ratio |
|---|---|---|---|---|---|
| 500 | 1,925 | 11,359 | 0.169 | — | — |
| 1,000 | 7,287 | 24,025 | 0.303 | 1.92 | 0.84 |
| 2,000 | 23,079 | 45,624 | 0.506 | 1.66 | 0.74 |
| 4,000 | 64,176 | 89,435 | 0.718 | 1.48 | 0.50 |
| 8,000 | 157,981 | 181,441 | 0.871 | 1.30 | 0.28 |

The ratio is climbing toward a ceiling of exactly 1.0 that the code guarantees, and **both local
exponents fall monotonically** — the call exponent from 1.92 to 1.30, the ratio's own from 0.84 to
0.28. More SKUs spread the same demand over more bins, so a larger share of reclaims are a SKU's
last bin in that aisle; once essentially all of them are, growth is linear in reclaims and nothing
is left to saturate.

The fitted power law is not merely pessimistic, it is **arithmetically impossible**: extending
k = 0.28 on the ratio puts it above 1.0 at roughly 13,000 SKUs. The ladder's whole span
(500–8,000) sits inside the ramp. A single number fitted across a saturation transient was read as
a complexity class for three weeks.

**What this cost, and what it saved.** The plan's fix was `delta_lift_sorted` — which already
exists, is called by nothing, and would have been a **named comparability break**, because numpy's
pairwise `.sum()` re-associates against the Python `sum()`. That is the full break protocol —
digest classification, a dated record beside the other six, a two-run DB comparison — spent to
convert linear into linear. The refutation cost no CPU at all: six archived captures for the
parent, two constants in the generator for the per-call cap, and one existing counter as the
denominator.

**What would re-open it.** Raising `cluster_size` or `top_k` raises per-call cost directly and is
the only thing that makes this superlinear again; both are catalogue-generation values that
ticket 05 moves into the profile's own metadata, where a change is recorded. A rung above ~13,000
SKUs should read k → 1.0; the meso ladder tops out at 8,000, so that confirmation is a rung, not a
refit — and the proof above does not depend on it.

### The same test, applied to the rest of the table

A saturating ratio leaves the same fingerprint anywhere: **a falling local exponent** against a
denominator that is known to be linear. `_index_add` is that denominator — one call per reclaimed
bin, k = 0.99 across the ladder — and every offender already carries its own call series in the
same artifact, so the whole table can be re-triaged with no run at all:

| offender | k | local k, first → last | ratio's local k, last | verdict |
|---|---|---|---|---|
| `delta_lift_idxs` | 1.59 | 1.92 → 1.30 | 0.28 | **saturating → linear** |
| `delta_lift_idxs.<locals>.<genexpr>` | 1.52 | 2.15 → **0.99** | −0.03 | **saturating → linear** |
| `sum_lift.<locals>.<listcomp>` | 1.32 | 1.48 → 1.18 | 0.16 | **saturating**, and 29,715 calls |
| `sum_lift` | 1.22 | 1.30 → 1.14 | 0.12 | **saturating**, and 32,582 calls |
| `Task.__init__.<locals>.<genexpr>` | 1.25 | 1.31 → 1.17 | 0.15 | decaying |
| `per_pick` | 1.26 | 1.25 → 1.51 | 0.48 | sustained — closed on price, §3 |
| `_TravelBalancedPool._aisle_best` / `._score_of` | 1.54 | 1.62 → **1.77** | **0.75** | **sustained, and accelerating** |

Five of the seven are decaying toward linear; two of those five are also too small to matter at any
scale on the ladder. **One offender in the whole table has a local exponent that rises**, and it is
the one this round already half-fixed — `_aisle_best`, whose surviving run-boundary rebuild is
priced at 2.66 % of a rung and whose attribution the deep ladder explicitly refused to confirm
(§2.3). Everything the instrument had to say about complexity, it was saying about that one site.

This is worth more than the single retraction. The offender table was ranked by a fitted exponent,
and a fitted exponent cannot distinguish a complexity class from a ratio on its way to a ceiling —
so the ranking put four converging series above the one diverging series for three weeks. The fix
is not a better threshold: it is **reporting the local exponents alongside the fit**, because the
trend within a ladder is the part that separates the two, and the ladder already computes it
(`_local_exponents`) and then shows it only for knees.

### The aggregate was linear and one family was pulling away

Refuting `delta_lift_idxs` emptied the meso offender table of everything but `_aisle_best`. That
should have been suspicious on its own: the meso ladder runs **one arm**, and §2.5 already records
that the assignment families are invisible to it. So the remaining candidates on the plan's list —
`_CoDemandPool`'s per-take scans, `_ClusterMapPool`'s `list.remove` — were checked against the HEAD
artifact and are **not in it at all**. Not small: absent. They belong to arms no cell reaches.

The deep ladder does run all 136 arms, and it recorded which one was slowest at each rung:

| max_skus | 10,000 | 20,000 | 40,000 | 60,000 | 80,000 |
|---|---|---|---|---|---|
| slowest arm | `opt_cluster_map` | `opt_cluster_map` | `uni_cluster_map` | `uni_cmin` | `uni_cmax` |
| its `total_s` | 36.1 | 71.0 | 144.8 | 258.3 | **410.9** |
| local k | — | 0.98 | 1.03 | 1.43 | **1.61** |
| `total_s_sum` over all 136 | 2,283 | 4,516 | 9,188 | 14,564 | 20,471 |

**The sum is linear at k = 1.05 across the same span where the worst arm goes from 36 s to 411 s
with a rising local exponent.** Every section exponent in the deep report is a sum over arms, so
the divergence is invisible in all of them — and the `reord_s` bend §2.3 could not attribute is in
the same rungs.

`cmax` is `MaxClu`, `cmin` is `MinClu`, `cluster_map` is `CluMap` (`strategies.py:331-336`): the
argmax is not wandering at random, it stays inside **one placement family** — the family whose two
pools the plan named on static grounds and the instrument has never once measured.

This is not yet a conviction, and the reason is worth stating precisely: **a max over a migrating
argmax is not any arm's growth curve.** A max over 136 noisy series is biased upward, and four arms
taking turns being unlucky would produce a similar picture. The artifact kept only the extremum, so
the question cannot be settled from it — which is the defect, not the finding.

Both halves are now fixed, and neither needed a run:

- **the ladder keeps `per_arm_total_s`** and fits every arm across the rungs. An arm missing from a
  rung is skipped rather than zero-filled, because `zip()` truncates in silence and turns a dead
  worker into a fast arm — measured, 4 points against 5 rungs gives k = 1.03 at r² = 0.999 where
  the truth is 1.05, and *pool run swallows dead arms* records that an arm can vanish from a run
  that still exits 0;
- **the cluster family has two cells**, `--config cluster_map` and `--config cmin`, so the meso
  ladder can trace those pools at all. This is the third time this exact blind spot has been paid
  for, after `--config` itself and the two ranked cells.

And it is the flagging rule that had to change, not the threshold. The diverging arm **fits
k = 1.15 over the whole span** — under `FLAG_TIME_EXP` (1.50) *and* under `FLAG_COUNT_EXP` (1.30).
No threshold on the fit reaches it, at any setting that would not also flag every arm in the run.
Its first two steps are linear and average the last two away; only the trend sees it.

### Inbound: one quadratic is unreachable, the other is bounded by a queue, not by scale

Both were on the plan's list as genuine `O(n²)` by inspection. They are — and neither is a
complexity risk, for reasons that are structural rather than measured, so no run was needed.

**`bounded_order` (`Inbound/priorities.py:225-228`) — the quadratic branch is dead in every
shipped configuration.** Its `while remaining:` loop, with `max(range(len(window)))` re-evaluated
per round and a mid-list `pop`, runs only when `bound` is set AND smaller than the candidate list.
`INBOUND_TRAILER_BOUND = None` (`settings.py:181`), and the other two call sites
(`transit.py:269, 436`, the per-pallet ones) pass `bound=None` explicitly — which takes the
`sorted(...)` path, `O(n log n)`. The archived captures confirm it from the other end: every traced
`bounded_order` call shows `sorted` among its children and 17 key evaluations for 17 candidates,
which is the sorted path's count, not the loop's `n × bound`.

Even with the bound set it cannot become quadratic **in scale**: `n` is the yard depth, and
*inbound yard is a stable queue under the era* records ρ = 0.819 with depth T ≈ 13 — a queue depth
set by arrival and service rates, not by the catalogue. The ladder axis does not reach it.

**`plan_order` (`Inbound/gain.py:1271-1299`) — genuinely `T³`, and `T` is that same queue depth.**
The round loop runs `T` times over a shrinking candidate list, so the two `place_load` sweeps cost
`T(T+1)` calls; worse, `others = {i for i, n in counts.items() if n > 1 or i not in ids}` and the
`ev.taken | others` union beside it are both rebuilt **per candidate per round**, which is the
cubic term. *Inbound yard is a stable queue* already names this — "drain cost is cubic in T,
confirmed at T = 12.97 depth".

The three mechanical fixes the plan proposed do not actually remove it, and it is worth recording
why so nobody re-derives it: hoisting the multi-count set out of the candidate loop still leaves
`others` proportional to `|counts|` per candidate, and the `ev.taken | others` union that follows
is proportional to `|taken|` regardless. The union is the floor. Removing it means giving
`place_load` a base-set-plus-exclusion pair instead of a materialized set — an API change to the
evaluator, which is why the plan called the structural half a separate research ticket and why it
stays one.

**What would re-open both.** Neither is a function of catalogue size; both are a function of
**ρ**. At ρ = 0.819 the queue is stable and shallow, but depth goes as `1/(1−ρ)` and `plan_order`
goes as its cube, so the trigger is a volume increase against fixed doors — a *capacity* question,
and the ladder's SKU axis will never show it however far it is extended. *Site dock is shared
across channels* records that door count is not currently a knob. The honest instrument for this is
a ρ sweep, and the one archived `yard` ladder is the known-bad artifact whose x-axis moved 4 %.

### Half the deep-tier wall is not per-arm work, and it is not a complexity problem

The deep ladder's own commensurability check — `Σ total_s / workers` against the measured wall —
has never read better than 0.52:

| max_skus | wall (min) | model (min) | gap | gap/wall | gap per arm-slot |
|---|---|---|---|---|---|
| 10,000 | 8.2 | 2.1 | 6.1 | 0.74 | **48.4 s** |
| 20,000 | 10.5 | 4.2 | 6.3 | 0.60 | **50.0 s** |
| 40,000 | 18.2 | 8.5 | 9.7 | 0.53 | 77.0 s |
| 60,000 | 27.0 | 13.5 | 13.5 | 0.50 | 107.2 s |
| 80,000 | 36.4 | 18.9 | 17.5 | 0.48 | 139.0 s |

That check exists to catch rows describing a different run than the clock did, and a ratio of 0.26
looks exactly like that failure. It is not. The last column is the decomposition: the gap divided
by arm-slots (`gap × workers / 136`) is **what one arm costs outside its own `total_s`** — and it
is ~48 seconds *flat* across the first doubling, then grows with the catalogue.

48.4 s × 136 arms ÷ 18 workers = **6.0 minutes**, against a measured gap of 6.1 at the smallest
rung. The fixed term alone accounts for essentially the whole gap there.

**The mechanism is already on record.** `max_tasks_per_child` is pinned at 1 — *worker recycling
pinned at one* records that anything higher deadlocked the pool at a cell boundary, so the pin is a
decision, not an oversight. One process per arm means every arm pays a fresh interpreter start, a
full re-import, and a catalogue load (the affinity CSR alone is 41 MB resident). The batch loop's
clock starts after all of that, which is why none of it is in `total_s` and none of it is in any
section. `precomp_s` is measured outside `total_s` too and is *not* the answer — it is 0.11–0.28
minutes per rung against a gap of 6.1–17.5.

**Three consequences, and the first two are reassuring:**

1. **The section exponents are not contaminated.** They are computed from per-arm sums, and the
   gap is per-arm *startup*, which is in neither. `total_s_sum` at k = 1.05 is measuring real work.
2. **The deep WALL is the number not to fit.** It grows at k = 0.72 across the ladder — sub-linear,
   because a largely fixed 48 s per arm amortizes as the work grows. Anyone fitting wall-clock here
   would conclude the simulation gets cheaper per SKU. It does not; the startup share shrinks.
3. **It is a scheduling cost, not a complexity one, and it is large.** At the top rung it is 17.5
   minutes of a 36.4-minute run. Halving it would nearly halve every deep ladder — but the lever is
   the recycling pin, which is held shut by a deadlock, so this is **recorded, not fixed**.

**What would make it actionable.** Not a faster catalogue load: the flat 48 s at the two smallest
rungs is mostly *not* catalogue-sized. The question is what a fresh worker pays before it starts
its batch loop, and that has never been measured directly — only inferred from this subtraction.
A single instrumented arm, timing interpreter start / import / catalogue load / first batch, would
settle it in one run of a few minutes. That is the cheap next step if anyone wants the 17 minutes
back, and it is a different investigation from anything in this document.

---

## 4. What now has a fence

**The heap's win had none.** `test_travel_balanced_equivalence.py` proves the pool's RESULT is
byte-identical to a frozen oracle — and a "simplification" back to a linear scan would keep every
result identical, pass the whole file, and restore the billion iterations. That is the shape that
let `_admit_held` survive every release.

`Tests/unit/test_placement_selection_is_not_a_scan.py` counts calls instead: the refresh is exactly
one per take at 3 and at 24 aisles; per-take work does not grow across an 8× aisle increase; and
the rebuild is exactly `sum(live aisles at each boundary)` — pinned so that changing it is a
DECISION, and so that a lazy-bound refactor shows up as a smaller number rather than as silence.

Its non-vacuity test is the load-bearing one: every other assertion is a bound satisfied by doing
LESS work, so an undercounting counter passes all of them.

**And the instrument itself is now gated.** `Tests/calltree/` was in no gate at all; the two
seconds-long files plus `test_digest_surface.py` are the tenth gate in `CLAUDE.md` (~16 s, 48
tests). `test_rank_cache_equivalence.py` is deliberately excluded — 7–13 minutes is a pre-merge
cost.

---

## 5. Method lessons this round paid for

1. **Read the tool's output, not just its results.** All three instrument defects were visible in
   printed output nobody had read closely; none was findable by reading code.
2. **A count is not a claim, and a subtraction is not an attribution.** Writing the refusal into
   the ticket *before* measuring was worth more than the measurement.
3. **Price before building.** Two candidates were closed on ten minutes of measurement each,
   saving a kernel signature change worth 0.86 %.
4. **The fixture must run what production runs.** Third instance in this repo, after `--config`
   leaving the put-away path structurally dead and the catalogue ceiling truncating top rungs. The
   shape is identical every time: the fixture and production diverged, and only the fixture was
   ever read.
5. **A guard that flags everything is a guard nobody reads** — and the ALL-ZERO warning was worse
   than useless, because the README teaches readers to trust that exact line.
6. **Check WHICH ARM a passing test exercises before believing it.** The ranked-assign oracle
   passed 42/42 on a heap it never ran. A green suite is evidence about the code under test,
   and "the code under test" is a claim that needs checking after any change to how the
   production object is constructed.
7. **An instrument can be blind for a good reason.** The C-leaf exclusion is correct on its own
   terms and still hides half the scans in this codebase. Knowing what a tool cannot say is
   part of reading it.
