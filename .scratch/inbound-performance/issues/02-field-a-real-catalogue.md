# Field a catalogue that is not one item per bin

Type: task
Status: resolved

User instruction, verbatim: *"Q should NOT equal 1 as a rule. Make the run real."* and, later,
*"You shouldn't have to make a completely new generator. But adjust the existing generator as necessary."*

## Question

The meso fixture was believed to collapse every SKU to one unit per bin. Where does that come from,
and what is the smallest honest fix?

## Answer

**The generator needed no change. The degeneracy was in the test fixture.**

### The first diagnosis was wrong, and is retracted

It was claimed that `_PUT_RECIPE`'s `coverage=2.0` floors essentially every SKU to `Q = 1`. Measured on
the synthetic builder at 600 SKUs, seed 7:

| recipe | Q median | frac(Q==1) | fits/pallet |
|---|---|---|---|
| `_PUT_RECIPE` (cov 2.0) | 8 | 0.128 | 2 (46% fit exactly one) |
| production (cov 10) | 39 | 0.025 | 2 (46%) |
| high (cov 40) | 158 | 0.010 | 2 (46%) |

`Q = max(1, round(coverage x f x q))`, and the synthetic catalogue's expected demand is ~4.1 units per
batch, so coverage 2.0 yields `Q = 8`, not 1. The claim came from a memory measured on the **production
400k catalogue** (~0.024 units/day/SKU), where it is true; it does not transfer to `_build_inventory`.

### The real cause is item geometry, and coverage cannot touch it

`fits/pallet` is **invariant** across every coverage setting above. `Order.__init__` calls `_sample_dim`,
which is `random.triangular(3, 48, 48)` — mode **at** the pallet footprint — so the median synthetic item
is 32,760 in^3 against a 110,592 in^3 pallet position. Coverage moves bins-per-SKU; it cannot move
units-per-bin.

Crucially, `Order(storage_type)` **bypasses the creation plan entirely**. The production generator
(`Warehouse/generation/generate_inventory.build_inventory_from_plan`) picks a `Family` by share and then
samples dimensions, weight and demand from that family's own specs — and several families are already
2-component mixtures (`electronic`: 60% N(8,2) + 40% N(34,5); `seasonal`: 50/50 two triangulars), with
weight laws varying per family and `BELL_FF_FREQ` a 3-normal mixture. That is already the
"two or three distributions per bucket" shape.

### The fix: run the existing generator

```
python -m Warehouse.generation.generate_mixed_profile --num-skus 40000 --seed 42 \
  --freq-profile bell --fulfillment-fraction 0.4 --lead-times 0 --name perf_mixed_40k
```

Same `CREATION_PLAN`, same bell profile, same `fulfillment_fraction` and `supply_cv_max` as the
production 400k profile's own `params_json` — just 10x smaller. 12 s to build, 13 BinKey groups,
~1.4M affinity rows, lands under the `PROFILE_INPUT_DIR` key.

Measured against the synthetic builder:

| | `perf_mixed_40k` | `_build_inventory` |
|---|---|---|
| median item volume | 1,526 in^3 | 32,760 in^3 |
| fits/pallet | median **4**, mean 4.4 | median 2, mean 2.1 |
| frac(fits == 1) | **0.145** | **0.458** |
| fits/singleton | median 1, mean 1.9 | median **0** — will not fit |
| fits/ff bin | median 2, mean 2.5 | n/a |
| composition | 23,880 store + **16,120 fulfillment** | 3,000 store, **zero** fulfillment |

The synthetic builder's items are so large a singleton bin cannot hold one, and it has no fulfillment
section at all — so every fulfillment-only inbound code path was unreachable from meso regardless.

## Comments

Side effect worth knowing: `find_latest_db_pairs()` now resolves `perf_mixed_40k` as newest, so anything
defaulting to "the latest profile" picks the 40k catalogue over the 400k one. Pin the profile explicitly
wherever it matters rather than relying on that ordering.
