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
