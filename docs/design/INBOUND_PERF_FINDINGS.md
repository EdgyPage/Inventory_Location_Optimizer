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
