# Inbound performance findings — the instrument, what it convicted, and what it acquitted

The `Inbound/` package was written between 2026-08-26 and 2026-09-13 and had never been measured.
This is what measuring it found. It is the successor to `STRESS_TEST_FINDINGS.md` and inherits that
document's most valuable property: **it retracts its own wrong findings in place.** Four are
retracted below. Read the retractions first if you are here to cite a number.

Reproduce with:

```bash
python -m pytest Tests/calltree -q                                              # the harness is honest
python Tests/calltree/calltree_capture.py --tier meso --config inbound_gain_pool
python Tests/calltree/calltree_growth.py --ladder meso --knob yard --config inbound_gain_pool
python Tests/unit/test_gain_cow_equivalence.py                                  # the byte-identity guard
```

---

---

## THE HEADLINE RETRACTION — read this before citing any multiplier here

**RETRACTED: "a pool-adapter gain cell costs ~1.1x a run, and the worry that chartered this
effort is not supported."** That was §1 and the map's destination clause. It is wrong.

Measured on the campaign's own catalogue (`mixed_20260816_131535`, 400,000 SKUs), coupled, on
a pool adapter, at **rho = 0.837** against the campaign's projected 0.819:

| skus | drain_p | **DRAIN x** | run_u | run_p | **RUN x** | **T** | pools | rho |
|---|---|---|---|---|---|---|---|---|
| 25,000 | 1.48 | 9.83 | 62.6 | 58.7 | 0.94 | 1.61 | 792 | — |
| 50,000 | 5.96 | 19.22 | 79.2 | 89.4 | 1.13 | 2.74 | 1,860 | — |
| 100,000 | 25.43 | 41.22 | 122.4 | 148.3 | 1.21 | 4.36 | 4,988 | — |
| 200,000 | 118.26 | 84.62 | 217.3 | 333.1 | 1.53 | 6.90 | 9,580 | 0.836 |
| 200,000 *(repeat)* | 118.79 | 84.38 | 223.9 | 333.5 | **1.49** | 6.90 | 9,580 | 0.836 |
| **400,000** | **956.17** | **349.07** | 423.4 | **1372.9** | **3.24** | **12.97** | 24,912 | **0.837** |

**3.24x, not 1.1x** — against the 1.63-1.93x that phase 2's 8.6-9.7 h sizing rests on. The top
rung repeats (200,000 run twice in separate invocations: 118.26 vs 118.79 s, 0.45% apart), so
this is not the single-data-point trap that cost this effort a knee finding and cost it §1.

Every earlier number here was measured on a ladder that could not reach the regime the campaign
runs in. The store leaf never stands a yard (T 1.14-1.30); the coupled ladder that followed was
capped at **T = 2.25 by its catalogue**, not by the code (§4). The drain is **cubic in T**, and
the campaign runs at **T = 12.97**.

**Since measured, and now 3.15x**: the `take` heap (section 4) took the drain -6.3% at
campaign scale, which carries the arm-slot-weighted campaign sizing to ~12.5 h at 4 workers
against ticket 31's 8.6-9.7 h. The restatement is `.scratch/inbound-performance/issues/
16-phase-2-restated-13-hours-not-8-6.md`.

What survives, and it is the reason this was findable: **the method**. Two multipliers with
different denominators, paired within a rung, with the commensurable one named. The DRAIN/RUN
distinction in §1 is correct and is still how to read this tool. The error was concluding from
a flat RUN column that the cost does not grow, when the ladder had simply stopped growing T.

---

## 0. The instrument did not exist, and two tiers were dead

Every symbol in `Inbound/` was **structurally unreachable from every runnable rung**. `build_assets`
bound a plain `Dock` on a `BatchTransit`, so `SiteReceiving`, `YardTransit`, `SpaceTimeline` and the
whole of `gain.py` never executed. A ladder reported the subsystem as costless and nothing
contradicted it — the same failure mode that let the `_admit_held` quadratic survive every release.

Four pre-existing breaks, all found by running the tiers rather than reading them:

| break | what it cost |
|---|---|
| `test_minlabor_cache_matches_frozen_oracle` failing on clean `develop` | `fc7a46a5` (ADR-0001) re-priced all four production minlabor sites and the SIBLING oracle in `Tests/unit/`, and missed the two in `Tests/calltree/` — which is in none of the nine gates. The two sides of the comparison were running different cost formulas, so the test accused a correct cache. |
| `run_fullfid` refusing outright | `max_bins=20000` binds below the era's declared levels. **No cap value would have worked**: the coverage fixed point declares before it converges and its seed round sizes ~11x the plan it settles on (898,700 bins before settling at 77,500). |
| `run_fullfid` unable to reach the ERA | it skipped `_derive_staffing_for_pair`, stage B. Stage A (coverage) already ran, which is why the era *looked* reachable. |
| `run_meso` never passing `now_s` | under a `BatchTransit` harmless; under a trailer pipeline a trailer with any positive lead **never arrives** (73 stuck in transit over 10 batches, zero unloads), and at lead 0 `arrived_s` stays None — which collapses fifo and lifo into a **byte-identical** unload stream and empties `gain_gated`'s urgent set at every threshold. |

### The fixture was degenerate, and not for the reason assumed

**RETRACTED:** *"`_PUT_RECIPE`'s coverage=2.0 floors essentially every SKU to Q=1."* Measured on the
synthetic builder at 600 SKUs: coverage 2.0 gives **Q median 8, 12.8% at Q=1**; coverage 10 gives Q
median 39, 2.5%. The claim came from a memory measured on the production 400k catalogue, where it
is true, and does not transfer.

The real degeneracy is **item geometry, and coverage cannot touch it**. `Order.__init__` samples
`random.triangular(3, 48, 48)` — mode AT the pallet footprint — so 46% of synthetic SKUs fit
exactly one unit per pallet position at *every* coverage. `fits/pallet` is invariant across the
whole coverage range.

And it is a property of the TEST FIXTURE, not the generator: `Order(storage_type)` bypasses the
creation plan entirely. The production generator already samples each family from 2-3 component
mixtures with per-family weight laws. Running it unchanged at 40k SKUs gives fits/pallet median 4,
14.5% at one, median volume 1,526 in³ against the synthetic 32,760 — and 16,120 fulfillment SKUs
the synthetic builder lacks completely.

---

## 1. What the evaluator actually costs, and what it does not

`plan_order` runs twice per drain (a gain arm sets both knobs to one name) and costs exactly
**T(T+1)** `place_load` calls. Measured on the real driver, `uni_rank_labor_norsl`, 2,000 SKUs, 25
batches, one leaf:

```
plan_order entries 48 | place_load 590 (12.3/entry) | _make_pool 7,426 (12.59 per place_load)
units to place_load 94,274 (159.8 per call) | candidates per entry 2.8, max 6
```

**RETRACTED: "12.59 pool opens per `place_load`, driven by the SPILL CHAIN."** Tier iterations ==
`_place_pool` calls == `_make_pool` calls == 7,426 exactly, with **zero** unseated units. 12.59 is
**BinKey GROUPS** per `place_load`. The spill chain averages 2.84 tiers and only its first entry is
ever used.

**RETRACTED: "a group is ~160 units."** 159.8 is units per `place_load`, spread over 12.59 groups.
A group is **12.70 units mean, median 3**. That is what makes `_place_merge`'s per-placement sort
trivial rather than significant.

### The receive-drain wall split

Two runs, reproducible to 0.08 percentage points, invariant to door count:

| | share | what it is |
|---|---|---|
| pool `__init__` over the tier | **59.0%** | 462 ns/candidate, 8,130,323 candidates |
| `cands` build (the `excluded` filter) | 12.0% | 93 ns/elem, 8,234,764 scanned, 1.27% removed |
| `live` rebuild | 8.9% | 70 ns/elem, 8,130,323 scanned, **0% removed** |
| take loop (the actual work) | 8.9% | 94,274 units |
| `pool.order` (LPT sort) | 0.68% | |
| `AISLE_VIEWS` | 0.26% | after the fix in §2 |
| `SpaceTimeline.freeze` | 0.8% | 2.28M copies, C-level `tuple()` at 21 ns |

Per adapter: POOL 6.11-6.37 s (8 of 12 phase-2 arm-slots), MERGE/`tmin` 2.88-3.26 s (2 of 12),
UNIFORM/`fifo` 1.41-1.60 s (2 of 12). The pool adapter is ~84% of a pair-set's drain.

### Acquitted by measurement — do not re-convict these

`_place_merge`'s sort, `place_load`'s `groups`/`avail_cache` rebuild, the `expect_heads` branch,
`_window_rates`, and `SpaceTimeline.freeze`.

**The method lesson:** element counts must be weighted by COST CLASS before ranking. The pool's
elements cost 6.6x the `excluded` filter's and 22x freeze's. An earlier ticket in this effort ranked
a candidate by raw element count and would have over-ranked it on exactly that mistake.

---

## 2. What landed

### Copy-on-write aisle views — 64-117x fewer copies, -45% to -78% on the drain

`_make_pool` copied up to six whole-warehouse aisle dicts per virtual placement. Measured before
building anything, over 5,534 pool opens: a pool touches **1.22 of 46** live aisles, the copy walked
all forty-six, and it cost **9,313,598 set-element copies** in twenty batches on one leaf.

`AISLE_VIEWS` now sits beside `AISLE_COPIERS`, asserted to cover each other at import. Floats copy
**nothing** (immutable: reads fall through, only writes overlay); sets and the two-level list dict
materialize one aisle per access. `values()`/`items()` stay eager deliberately — a lazy one would
hand out the live container, the exact failure the copy exists to prevent — so `rank_random`, which
unions `aisle_idx_sets.values()`, gets correctness and no win.

| arm | elements copied | drain wall (paired, interleaved) |
|---|---|---|
| `rank_labor` | 1,379,360 → 11,780 (117x) | 0.069 → 0.042 s, **-45.5%** |
| `rank_cartlabor` | — | 0.058 → 0.027 s, **-49.3%** |
| `rank_minlabor` | 1,890,588 → 29,616 (64x) | 0.353 → 0.090 s, **-77.7%** |

`rank_minlabor` is the headline and the arm that matters most: its eager drain was FIVE TIMES the
others, and it is fulfillment's #1 and store's #2 in `PHASE2_PAIRS`. Read -45% and -49% as FLOORS —
the timed span includes `freeze` and `compose_site_view`, which this does not touch.

### The `live` rebuild — 8.1M scans, zero removals

`used` is non-empty only when a later BinKey group spills into a tier the same placement already drew
from. Measured zero times in 7,426 opens. Guarded, not deleted: "never happens" is a measurement of
today's spill behaviour, not an invariant the code declares.

---

## 3. What was built and NOT landed

**The candidate slice.** The pool opens over 1,094.8 candidate bins to place a median of **3** units.
Slicing to k-per-bucket is byte-identical — all four adapters digested identically over counters,
every occupied bin and the live aisle dicts — and cut candidates from 8,130,323 to 432,177 (18.8x).

It was reverted because it is a **regression in the middle of the range** and no gate removed that
without trading one scale for another:

| | unguarded | gate@4 opens | gate@32 opens |
|---|---|---|---|
| 600 SKUs | +128.3% | -1.0% | — |
| 2,000 SKUs | +20.5% | +32.2% | +14.2% |
| 6,000 SKUs | -48.1% | -42.7% | -17.2% |

**The transferable mechanism:** the win is DOWNSTREAM (the pool's `__init__` handles 58 candidates
instead of 1,370) and the price is LOCAL (a C-level comprehension replaced by a Python selection
loop). So the trade is pure amortization and the separating variable is **opens per tier** — not
tier size or oversize ratio, which barely move between the last two rungs while the sign flips.

A future attempt should drop the memo and bucket in ONE pass per open. That needs the pool to accept
pre-bucketed input — a signature change in `Assignment_Functions` that touches the restock path.

---

## 4. The yard: three statements, two of them retracted

**RETRACTED: "production never stands a yard."** It rested on `RECV_DAY_SECONDS = None`, read in
isolation. That constant is the SECOND of two branches (`strategy_runner.py:1676-1682`); under
`shift_drain_or_cap` — which `ERA_RUN_DEFAULTS` sets and `PHASE2_RUN_DEFAULTS` inherits — the
receiving day comes from the site-wide shift and the constant is never consulted.

**RETRACTED: "whenever the whistle binds at all, depth compounds with batch count."** A queue
compounds at ρ ≥ 1 and settles at ρ < 1. The 10-20 SECOND whistles that produced yard depth 391 put
the crew far above ρ=1.

**What stands, measured under the era:**

```
recv_deadline = 28800.0 at every drain          (flag-off: None at every drain)
drain load / (crew × 28,800 s) = 1.000–1.008 on 6 of 40 drains — it BINDS
flag-off control reaches 13.04 — unbounded, the no-whistle signature
yard depth after the drain: 0 on all 200 instrumented drains
coupled, 40 site days: drain-start depth max 2/2/4/5, candidates per entry
1.37/1.39/1.71/2.33 at 5k/10k/20k/40k SKUs
```

ρ is low at these sizes for a reason that is not the whistle: `crew_size` takes a `ceil`, so a small
catalogue floors the receiving crew at 1 — measured ρ_recv 0.044/0.095/0.211/0.427/0.445/0.619 at
N = 1k..40k. **The campaign's projected ρ_recv is 0.819**: high, still below 1. The campaign's yard
is a stable queue with a finite mean depth.

### Consequence for any future ladder

T is a queueing quantity in ρ, and **ρ(N) is a non-monotone sawtooth** from that same `ceil` —
projected 0.446, 0.647, 0.525, 0.619, 0.694, 0.837, 0.819 at N = 5k..400k. **Fitting T against
catalogue size is not a fit at all.** A ladder must fit against measured ρ and must reach ρ ≈ 0.82,
which first happens near 160k SKUs. Separately: below ~100k the warehouse is the per-bucket **aisle
floor**, not the catalogue, so a ladder topping out at 40k fits the floor.

---

### The coupled ladder ran — and its first four rungs were not four rungs

The consequence above was written before the coupled ladder existed, and it called both
shots. The ladder ran `--coupled --rungs 5000 10000 20000 40000` on a quiet host:

| skus | drain_u | drain_p | **DRAIN x** | run_u | run_p | **RUN x** | T |
|---|---|---|---|---|---|---|---|
| 5,000 | 0.052 | 0.203 | 3.89 | 18.2 | 17.8 | 0.97 | 1.00 |
| 10,000 | 0.103 | 0.391 | 3.78 | 27.8 | 26.1 | 0.94 | 1.00 |
| 20,000 | 0.132 | 1.069 | 8.10 | 35.4 | 37.0 | 1.05 | 1.27 |
| 40,000 | 0.265 | 4.389 | **16.59** | 57.6 | 77.3 | **1.34** | **2.25** |

Read as a trend that says the run-level multiplier tracks yard depth, and that the uncoupled
store-leaf ladder reported flat 1.08-1.12 only because the store leaf never stands a yard.
It was read that way. Then the top rung was repeated, which is the whole reason a top rung
gets repeated:

| skus | drain_p | place_ld | pools | T | maxdep | DRAIN x | run_u | run_p | RUN x |
|---|---|---|---|---|---|---|---|---|---|
| 40,000 | 6.184 | 132 | 1,282 | 2.25 | 3 | 16.19 | 72.1 | 96.1 | 1.33 |
| 60,000 | 6.044 | 132 | 1,282 | 2.25 | 3 | 15.95 | 91.4 | 90.8 | 0.99 |
| 80,000 | 6.001 | 132 | 1,282 | 2.25 | 3 | 16.66 | 81.4 | 82.8 | 1.02 |

**Every priced quantity is identical at all three rungs** — same `place_load` count, same
pool count, same yard depth, same max depth, drain within 3%. They are one run measured three
times, and identical pricing cannot produce RUN x of 1.33, 0.99 and 1.02.

**The catalogue was the ceiling.** `run_fullfid` took `pairs[0]` of `find_latest_db_pairs`,
and the latest catalogue declares **40,000 SKUs**; 150,000 and 400,000 ones were sitting
beside it unused. `--max-skus` above the catalogue is not an error and not a warning — it
takes everything, which in the output is indistinguishable from a subsystem that stopped
growing. This is [a bin cap is self-defeating] inverted: there a cap **below** the
declaration refuses loudly; here a declaration **above** the fixture was truncated in
silence. Fixed by letting the declaration pick the fixture —
`run_fullfid(min_catalogue=N)` binds the most recent catalogue DECLARING at least N SKUs
(read from `run_metadata.params_json['num_skus']`, not counted, so a truncated table cannot
agree with itself), the ladder sizes that floor on its **top rung** so one catalogue serves
every rung, and a rung that still exceeds its catalogue prints `SATURATED`. Binding per rung
would have been worse than the bug: the catalogues are months apart in generator vintage
(40,000 is 2026-09-13, 400,000 is 2026-08-16) and the ladder would report that as growth.

**And the RUN column needs a quiet host, which the repeat did not have** — it ran alongside a
13-minute CPU-bound pytest. The DRAIN column survived (it measures a section, and reproduced
to 3%); the wall did not, reading 72.1 / 91.4 / 81.4 s for identical unpriced work. Four
samples of the same quantity gave run differences of +19.7, +24.0, -0.6 and +1.4 seconds.

> **RUN x is not resolvable by this instrument at this scale.** The pricing costs ~5.8 s of
> drain; the run wall varies by more than that between otherwise-identical invocations.
> Ticket 31 section 5 already recorded that no absolute wall survives a comparison across runs
> here, and pairing within a rung does not rescue it when the two poles are sequential
> subprocesses and the host is busy.

**What survives, and it is not nothing:**

1. **DRAIN x rises with yard depth**, reproducibly — 3.89 and 3.78 at T = 1.00, 8.10 at
   T = 1.27, and 16.59 / 16.19 / 15.95 / 16.66 at T = 2.25. Four independent measurements of
   the T = 2.25 point agree to 4%.
2. **`entries` is constant at 18 across every rung.** Drains do not multiply with the
   catalogue; the work per drain grows. An exponent on drain seconds alone would have said
   "receiving got more expensive" when the true statement is "each drain did".
3. **Section 1 stands, with a better reason** — not "the multiplier is flat" but "the pricing
   is a few seconds of drain against a wall whose own variance exceeds it".

Both halves of the consequence above are now load-bearing rather than predictive: a ladder
topping out at 40,000 fits the **aisle floor**, not the catalogue, and it never reaches the
campaign rho of 0.82.

---

### The mechanism: the drain is cubic in yard depth, and the cube decomposes

Fitted over a 16x span on one catalogue, five rungs:

| quantity | vs | k | r^2 |
|---|---|---|---|
| drain seconds (priced) | skus | 2.30 | 0.993 |
| yard depth T | skus | 0.74 | 0.997 |
| **drain seconds (priced)** | **T** | **3.13** | **0.998** |
| `_make_pool` calls | T | 1.67 | 0.996 |
| seconds per pool open | T | 1.46 | 0.976 |
| `place_load` per entry | T | 1.81 | 0.999 |
| run wall (unpriced) | skus | 0.70 | 0.970 |

pools ~ T^1.67 times seconds-per-open ~ T^1.46 is T^3.13 — the fitted value to two decimals.
The three factors are separable and each names a different piece of code:

1. **`place_load` per entry ~ T^1.81** is `plan_order`'s O(T^2) greedy over yard depth. It is
   doing what it was written to do; the yard simply got deep.
2. **pools per `place_load` is flat at ~10** across the whole range — the tier loop is not the
   growth term, which is what ticket 03's decomposition correction already established.
3. **seconds per pool open ~ T^1.46** is the term still on the table. Copy-on-write (§2) took
   the aisle-dict copy out of it; what remains is the candidate scan, which is exactly what
   ticket 10's slice attacks.

**`entries` is constant at 18 at every rung.** Drains do not multiply with the catalogue — all
of this is work per drain. An exponent on drain seconds alone would have said "receiving got
more expensive" when the true statement is "each drain did" ([a count is not a claim]).

**And above 100k the drain IS the run.** Run delta against drain delta: -34%, 55%, 96%, 101%,
100% at 25k / 50k / 100k / 200k / 400k. At 400,000 the receive drain is **956 s of a 1,373 s
run**. The small-rung noise that made an earlier repeat conclude "RUN x is not resolvable" was
real and is now beside the point: the signal is 950 s against a ~20 s wall variance.

### What this reopened, and then closed

The T^1.46 per-open term pointed straight at ticket 10's candidate slice, which was rejected
on a range topping out at 6,000 SKUs. Measuring it at campaign scale **refutes it**, and three
of ticket 10's premises are scale-dependent in a way the ticket does not say:

| ticket 10 | at 400,000 SKUs |
|---|---|
| `__init__` is **59%** of the drain | **28.8%** (57.2% at 3,000 — the figure reproduces, it just does not carry) |
| median **k = 3** units per open | **194.80** takes per open |
| **18.8x** fewer candidates | **1.5x** at the real k |

The k correction is the serious one. Ticket 10's byte-identity argument is "a pool asked to
seat k units performs at most k pops, so the (k+1)-th entry of any bucket is unreachable" —
true, and the whole reason a slice can be byte-identical. At k = 195 entries 4 through 195 are
reachable, so a slice keeping 3 per bucket would have been byte-DIFFERENT. The recorded digests
would have caught it, after the build.

The two ceilings compound: the slice touches only construction (28.8%) and saves only 1.5x
within it, so **92 s of 962 s — RUN x 3.24 -> 3.05**. Not worth three load-bearing orderings.

### RETRACTED: "the 71.2% is one billion iterations of a linear scan"

That claim was written here, acted on, and disproved by acting on it. `take`'s aisle scan was
replaced with a `(score, rank)` heap — byte-identical, proven against a three-way oracle — and
the drain fell **6.3%**, not 71%. The scan was **9.9%** of the non-construction drain.

Two errors, and the second is the one to carry:

| | claimed | measured |
|---|---|---|
| scan width | 224 (buckets) | **159 (aisles)** — `take` iterates aisles |
| iterations | 1.087 B | **771.6 M** |
| per iteration | 0.630 us | **0.080 us** |
| share of non-init drain | 100% | **9.9%** |

**The 0.630 us was obtained by dividing the very total it then claimed to explain.** A division
closes to three digits wherever the time actually goes, which is why this document warned
against exactly that two sections earlier and then did it anyway. The corrected figure closes
for the right reason: 771.6 M iterations (counted) x 0.080 us (saving / iterations, both
measured independently) = 61.5 s = the measured saving.

### What the drain is actually made of, and it has no dominant term

At 400,000 SKUs, coupled, after the heap:

```
priced drain 901.3 s  =  pool construction  277.6 s  (30.8%)
                      +  the aisle scan      61.5 s  ( 6.8%)   <- removed by the heap
                      +  everything else    562.2 s  (62.4%)
```

Nothing here is a single lever. The candidate slice attacks the 30.8% and is refuted inside it;
the heap took the 6.8% and is landed; the 62.4% is unattributed and would need a tracer, which
this tier cannot afford at campaign scale (tracing costs ~40x wall — a traced 400k run is about
fifteen hours).

**The one structural candidate that IS measured** is the SKU-run boundary rebuild. Per open at
400,000 SKUs: 56.0 boundaries x 159 aisles = **8,904 score computations to serve 194.8
placements** — 45x more scores computed than placements made. The heap cannot touch it (it is
the `R x A` term, and `K / (K + R)` = 78% is the heap's structural ceiling on selection alone).
Within it, `_aisle_best` calls `per_pick(m, intercept, var, 1, per_item)` once per (aisle,
bracket) for a value that depends only on `m` and `var` — ~223 calls per boundary for ~1.4
distinct values.

---

## 5. Three gate holes, one shape

Each failed silently **in the direction of looking healthy**:

1. A frozen oracle re-priced in production and not in the test tier.
2. A sabotage test (`test_the_identity_copier_lets_the_virtual_placement_reach_the_warehouse`)
   installing its identity into a table production had stopped reading — so the sabotage reached
   nothing. It failed loudly only because the refactor moved the seam under it.
3. Two flow anchors (`yard_plans`, `dock_plans`) whose symbols both resolved and whose RELATIONSHIP
   was wrong: `plan_order` is a great-grandchild of `yard_order`, not a direct child. They read 0 for
   their whole life, and `test_flow_anchors_resolve` — which names this exact failure mode in its own
   docstring — could not see it, because it resolves symbols against modules and never looks at a tree.

**A symbol table whose entries encode a RELATIONSHIP cannot be verified by resolving SYMBOLS.** The
check that catches all three is the same: exercise the thing and assert it produced something.
`test_every_flow_anchor_that_should_fire_does_fire` and
`test_the_carve_is_a_partition_and_is_inert_when_empty` are that check.

---

## 6. What this document does NOT claim

* **The campaign multiplier.** `inbound-optimization` ticket 31 measured a gain cell at 1.6-1.9x an
  unpriced one — on `('fifo','tmin')`, **the only two adapters that open no pool**. Eight of
  `PHASE2_PAIRS`' twelve arm-slots are pool adapters. That multiplier is not re-measured here, and it
  is the number that would restate phase 2's 8.6-9.7 h sizing.
* **Production scale.** The wall measurements are meso-tier, 600 SKUs, drains of tens of
  milliseconds near timing resolution. Direction and rough magnitude are established; the
  production-scale number is not.
* **Anything coupled.** `run_fullfid` takes `_channel_runs[0]`, which is always the store — and store
  binds 14-17 of 75 drains against fulfillment's 46-60. `PutawayPool`, the two-leaf
  `compose_site_view` and `_unload_split`'s door teams were not exercised.
* **The era at scale.** `run_fullfid` now reaches the era, but the measurements above at 5k-40k are
  well below the campaign's 400,000 SKUs at 40 site days.

## 7. A per-trailer key does not rank the yard the way `gain` does (2026-09-18)

W8 stage 3 proposed an O(T) "unload value" per trailer — a sum over the load with no pool, no
`SpaceView` and no bundle — to stand in for the cubic greedy in `Inbound.gain.plan_order`, and
set its gate as Kendall tau against `gain` before persisting anything. Measured with
`Tests/calltree/unload_key_tau.py` (kept, with a test in gate 10), which wraps `plan_order` on
the inbound ladder's own workload (`gain_forecast`, the era, receiving crew 4, uncoupled) and
scores every candidate at every drain with four keys, observation only:

| rung | `plan_order` calls | yard depth | verdict |
|---|---|---|---|
| 5,000 SKUs, 10 or 20 batches, crew 4 or 1 | 18–38 | always 1 | nothing to rank |
| 40,000 SKUs, 10 batches | 18 | 1 (16), 2 (2) | nothing to rank |
| **200,000 SKUs of the 400,000 campaign catalogue, 10 batches** | 18 | **3–7, median 4** | below |

| key | exact | top-1 | tau (n ≥ 3) mean / median | min |
|---|---|---|---|---|
| labour mass aboard, Σ freq·qty·labor_cost | 0/18 | 9/18 | +0.03 / +0.20 | −1.00 |
| demand mass aboard, Σ freq·qty | 0/18 | 4/18 | −0.04 / 0.00 | −0.33 |
| units aboard | 0/18 | 4/18 | −0.05 / −0.07 | −0.33 |
| FIFO by arrival, the control | 0/18 | 5/18 | +0.01 / 0.00 | −1.00 |

**Refuted.** No key reproduces a single drain, and only labour mass beats chance on the top pick
(half, where chance at depth four is a quarter). `gain`'s order is the CONTENTION term — each load
priced against what the other candidates leave standing — which a sum over one load cannot
carry; §1's fidelity ladder (ticket 04) already put the cost of dropping contention at tau
0.58–0.94, and a key drops space entirely. The persisted `unload_value` table and its declared
Quantity are therefore not built: they would record a number that ranks trailers unlike the
evaluator. If the cubic drain must get cheaper, the fallback the plan named stands — an L2-style
pool-free, contention-aware rung, which is an evaluator and not a key.

Two facts to carry: contention is a property of the catalogue's arrival rate against four doors,
so the question cannot be asked below ~100k SKUs (which is also why this document's first ladder
read flat); and labour mass aboard is a weak prior worth trying as a tie-breaker inside an L2
rung, not as a ranking.
