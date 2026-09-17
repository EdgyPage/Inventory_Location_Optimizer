# 21 - the pools take loose dicts, not a ledger

Type: refactor
Status: open
Blocked by: 04

Split out of ticket 02 stage B, which deliberately did not do it.

## What is left

Every pool builder still takes the aisle books as separate positional parameters:

```python
_build_travel_balanced_pool_fn(affinity, wp, aisle_sku_sets, aisle_idx_sets,
                               aisle_demand_sum, aisle_pick_load_sum,
                               sku_pick_load_product, freq_by_sku, qty_by_sku, ...)
```

and binds an `AisleLedger.over(...)` to them internally. Taking `ledger` instead would make
every one of these signatures NARROWER, and it would delete three hand-written lists that
restate the same fact:

- `Optimization/config/strategies.py` -- twelve call sites naming `mgr._aisle_*` one dict at
  a time;
- `_gain_bundle_for`'s per-family `aisle_state=` dicts (`ranked3`, plus the labour families'
  additions), which name the books each arm's pool commits to;
- `Tests/unit/test_gain_bundle_labor_families.py`'s `FAMILIES` table, which names them again.

## Why ticket 02 did not do it

**The frozen oracles.** The three placement equivalence suites carry hand-copied `_impl`
bodies and call the impls and the pool constructors directly, with the same positional
dicts. Changing a signature forces the oracle to be rewritten, which re-freezes it against
the very change it exists to check -- and those suites are the only thing standing over
`Warehouse/placement/`.

So this is not a hard blocker, it is a sequencing one: **do it in the same pass as ticket
04**, which collapses the ranked `_impl` twins and has to re-freeze those oracles anyway.
One re-freeze, with `run_digest.py` DB-row neutrality as the instrument that can actually
fail (the oracles cannot -- ticket 01 established they pin pool-vs-wave agreement and a
shared-state change moves both halves together).

## What is already safe without it

Not urgent, and this is why. The hazard `_gain_bundle_for` warns about -- *"a dict left off
that list is not a refusal, it is a virtual placement advancing the REAL warehouse"* -- is
now closed from both ends:

- a pool can only write the books its ledger was handed; every other book is a shared
  `_UnboundBook` that REFUSES the write and says why;
- `strategy_runner` checks `AisleLedger.POLICY_BOOKS` against `Inbound.gain.AISLE_VIEWS` at
  import, so a writable book with no copy-on-write view refuses to load.

What remains is duplication, not danger.

## Watch for

`AisleLedger.over()` is on the gain evaluator's per-virtual-placement path and was measured
at 0.29 us, 4.2% of a pool open (it was 2.00 us / 23.4% before being spelled out). If the
pools take a ledger built once per BUILDER, production stops paying it per pool open -- but
the gain evaluator will not benefit, because `_gain_bundle_for`'s factories call the builder
per virtual placement too. Do not claim that win without measuring it.
