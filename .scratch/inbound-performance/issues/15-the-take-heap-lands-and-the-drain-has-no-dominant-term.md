# The `take` heap lands: byte-identical, -6.3% — and the drain has no dominant term

Type: task
Status: resolved

The scope decision in ticket 14 was taken: widen to `Assignment_Functions` and replace `take`'s
linear aisle scan with a heap. It is built, proven byte-identical, measured, and landed. It is
also **much smaller than ticket 14 predicted**, and finding out why is the more useful half of
this ticket.

## What landed

`_TravelBalancedPool.take` selected the min-scoring aisle with `for aid in by_aisle:` — an O(A)
scan on every placement. It now pops a `(score, rank, aid)` min-heap.

**The tie-break is what makes it byte-identical rather than merely equivalent.** The scan kept the
FIRST minimum in dict-insertion order (strict `<`, `for aid in by_aisle`), and a binary heap is not
stable on equal keys. Entries therefore carry `rank` — the aisle's first-appearance index,
captured once in `__init__` — so an equal score falls through to the smaller rank, which is the
earlier insertion, which is the aisle the scan kept. Ranks are unique, so `(score, rank)` is a
strict total order and the third element is never reached by comparison. Ties are not exotic here:
a load balancer starts with `_load` equal across geometrically identical aisles.

**No lazy deletion, which is the usual cost of this pattern.** The heap holds exactly ONE entry
per live aisle: the run boundary seeds one each, every placement pops the winner and pushes back at
most one refreshed entry, and a non-winning aisle is never touched — the class docstring already
argues that its inputs are frozen within a SKU run. An exhausted aisle leaves by simply not being
pushed back, which is exactly the `continue` the scan did on a None `_aisle_best`.

**`_score_cache` became write-only and is gone.** With the argmin reading the heap and `_ab_cache`,
nothing read it; it was a dict write per placement for a value with no consumer.

## The proof

`Tests/unit/test_travel_balanced_equivalence.py` runs THREE implementations — a frozen in-file
oracle, the production closure `_travel_balanced_impl`, and the pool — and compares the (unit, bin)
sequence by identity plus every mutated state dict. `_travel_balanced_impl` was deliberately NOT
changed, so the comparison still has three independent references rather than two.

**The gate was proven able to fail before it was trusted:** reversing `rank` fails 5 tests,
including both `test_crafted_exact_ties` variants (which force exact cross-aisle score ties) and
`test_two_order_objects_one_sku`.

**And the counts are identical at campaign scale**, which is a stronger statement than the unit
oracle can make: a 400,000-SKU coupled run produces the same 3,262 `place_load` calls, 24,912 pool
opens, 13,621 candidates per open, yard depth 12.97 and max depth 23 before and after.

One limit on the oracle, worth recording because it was overstated first: it is frozen in SHAPE,
not in arithmetic. Its loop structure and strict-`<` argmin are its own, but `_D_map`, `per_pick`,
`height_multiplier` and `sec_per_inch` are imported from production, so a refactor of THOSE moves
the reference in lockstep. This change touches none of them.

## The result, with its controls

400,000 SKUs, coupled, one catalogue, quiet host. The unpriced pole and `init_s` are the controls
— neither can be affected by a change to `take`:

| | baseline | after | |
|---|---|---|---|
| unpriced wall (control) | 421.2 s | 418.6 s | -0.6% |
| `init_s` (control) | 277.01 s | 277.64 s | +0.2% |
| **priced drain** | **962.08 s** | **901.25 s** | **-6.3%** |
| priced wall | 1,381.5 s | 1,319.9 s | -4.5% |
| RUN x (paired, own poles) | 3.280 | 3.153 | -3.9% |

Reproduced twice: a second post-change run gave 895.36 s, agreeing to 0.7%. Two earlier attempts
were discarded because the unpriced control moved +53% and +21.6% — both times a heavy job of mine
was sharing the host, and the control is the only reason that was visible rather than reported as
a regression.

## Why it is 6.3% and not 71%, and the attribution that now closes for the right reason

Ticket 14 claimed the scan WAS the 71.2%. Removing it entirely disproved that. The corrected
figures, each measured independently of the total:

| quantity | ticket 14 | measured |
|---|---|---|
| scan width | 224 (buckets) | **159 (aisles)** — `take` iterates aisles, not buckets |
| iterations | 1.087 B | **771.6 M** |
| per iteration | 0.630 us (a division) | **0.080 us** (saving / iterations) |
| share of non-init drain | 100% | **9.9%** |

771.6 M x 0.080 us = 61.5 s, which IS the measured saving (685.07 -> 623.61 s). Ticket 14's
version divided the very total it claimed to explain, so it closed to three digits wherever the
time actually went — the trap that ticket names two sections into itself.

**The drain has no single dominant term.** What is now measured:

```
priced drain 901.3 s  =  pool construction  277.6 s  (30.8%)
                      +  the aisle scan      61.5 s  ( 6.8%)   <- removed by this change
                      +  everything else    562.2 s  (62.4%)
```

## The next candidate, and it is structural rather than a micro-fix

Per open at 400,000 SKUs: **56.0 SKU-run boundaries, 159 aisles, 194.8 placements.** Each boundary
rebuilds EVERY aisle's `_aisle_best` and `_score_of`:

```
56.0 boundaries x 159 aisles = 8,904 score computations per open
                               194.8 placements per open
                               -> 45x more scores computed than placements made
```

The heap cannot touch this — it is the `R x A` term, and `K / (K + R)` = 78% is the heap's
structural ceiling of the SELECTION cost only. The rebuild exists because `var` (the SKU's
`handle_var`) changes at a boundary and every aisle's best-bracket cost depends on it.

Two observations for whoever takes it:

1. `_aisle_best` calls `per_pick(m, intercept, var, 1, per_item)` once per (aisle, bracket), but
   that value depends only on `m` and `var` — not on the aisle. With 159 aisles over ~1.4 brackets
   there are ~223 calls per boundary for ~1.4 distinct values. Memoizing on `(m, var)` is
   byte-identical by construction (a pure function on identical arguments).
2. The deeper question is whether the whole-aisle rebuild is needed at all. Only the SKU-dependent
   factor changes at a boundary; the D-dependent part of each aisle's best bracket does not.

Neither is attempted here, because landing two changes at once would make it impossible to say
which one paid — and this effort has already spent three retractions on exactly that kind of
conflation.
