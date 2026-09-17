# 21 - the pools take loose dicts, not a ledger

Type: refactor
Status: resolved
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


---

## RESOLVED 2026-09-17

Every pool and wave builder takes an `AisleLedger`. Measured across eight of them, **91
parameters become 68**; `Assignment_Functions.py` loses 218 lines of signature and threading
against 150 gained.

### The line: COMMITS vs READS

The ledger carries the AISLE books a family commits to -- exactly `PlacementPolicy.ledger_terms`
-- and `sku_pick_load_product` / `sku_vol_product` stay their own parameters, because `take` only
READS them. That is not a convenience: it is the same distinction that keeps them out of the gain
evaluator's copy list, so the signature and the copy list now say the same thing by construction.

`ctx.ledger` is built once per policy in `strategy_runner._timed_build`, through
`state_names` -- the one spelling `_gain_bundle_for` already used -- so the mapping between the
manager's `_aisle_*` names and the ledger's book names lives in exactly one place.

### TWO CORRECTIONS

**1. Two of the three duplications were already gone.** This ticket lists three hand-written
lists that restate the same fact: `strategies.py`'s twelve call sites, `_gain_bundle_for`'s
per-family `aisle_state=` dicts, and `test_gain_bundle_labor_families.py`'s `FAMILIES` table.
Ticket 03 already derived the last two from the records. What was actually left was the
SIGNATURE WIDTH and the twelve call sites, which is a smaller prize than the ticket describes.

**2. The sequencing window had already closed, and that is the expensive part.** This ticket
says, in bold: *do it in the same pass as ticket 04 ... one re-freeze, with `run_digest.py`
DB-row neutrality as the instrument that can actually fail.* Ticket 04 landed in `7d0dd376`
earlier the same session, before 21 came up in the queue -- so this pass paid the SECOND oracle
re-freeze the sequencing existed to avoid. The cost landed exactly where the ticket predicted:
nine test files of driver plumbing, against roughly one afternoon of engine work.

The saving ticket 04's own record promised did hold: "the signature change it needs now has one
implementation per family to move instead of two."

### How the frozen oracles were handled

THREE of them, not one: `_oracle_travel_balanced_impl` in
`Tests/unit/test_travel_balanced_equivalence.py`, and `_oracle_ranked_minlabor_impl` /
`_oracle_co_demand_ranked_impl` in `Tests/calltree/test_rank_cache_equivalence.py`. For the
rank-cache pair the adaptation is ONE place -- `_wave_pool_fn`, the shim that stands in for a
`_build_*_pool_fn` -- because both bind the same four books.

`_oracle_travel_balanced_impl`'s BODY IS UNTOUCHED. It is a hand-copy of the retired algorithm,
and rewriting it to fit a new signature would re-freeze it against the very change it exists to
check. It gets a thin `_oracle_as_impl` adapter that unpacks the ledger into the frozen
positional form, so the reference stays the retired algorithm rather than a paraphrase of the
new one -- the same rule ticket 04 applied when it moved `_ranked_assign_impl` into its test.

### Two bugs the suites caught that review did not

1. `_build_co_demand_place_one` unpacked three books and its body read a fourth
   (`aisle_member_pos`) -- a `NameError` only on the cluster path.
2. `_ranked_assign_impl` handed a ledger to `_RankedAssignPool`, which is a POOL CLASS and still
   takes the three dicts. Only the BUILDERS narrowed; the classes did not, and conflating them
   is the obvious mistake to make here.

Both are the reason the equivalence suites are worth their runtime.

### The perf claim this ticket warns about, NOT made

The ticket says: *"If the pools take a ledger built once per BUILDER, production stops paying
`AisleLedger.over()` per pool open -- but the gain evaluator will not benefit, because
`_gain_bundle_for`'s factories call the builder per virtual placement too. Do not claim that win
without measuring it."* It was not measured, so it is not claimed. The structural change is real
(`over()` now runs once per policy on the production path instead of once per builder call); the
evaluator's per-virtual-placement cost is unchanged by construction, since `_make_pool` still
rebuilds the policy every time.

### Verification

| check | result |
|---|---|
| `Tests/unit` + `Tests/integration -k "not gpu"` | 3,248 passed / 2 skipped |
| the three placement equivalence suites + pool/policy/gain suites | 577 passed |
| `Tests/calltree/test_rank_cache_equivalence` (pre-merge) | **6 passed in 7m28s** |
| toy run vs baseline, `run_digest.py` | **IDENTICAL**, 136 arms |
| all ten gates | green |

`run_digest.py` is the instrument that carries this change. The oracles cannot fail it -- ticket
01 established they pin pool-vs-wave agreement, and a shared-state change moves both halves
together -- which is precisely why the ticket named DB-row neutrality as the bar.

### What this unblocks

Nothing. 21 was the last of the tickets split out of 02.
