# 02 - `_all_idx` is rebuilt from every aisle on every pool open, and the evaluator opens T(T+1) pools per drain

Type: task
Status: resolved

**Seen 2026-09-18 in the campaign's first priced cell** (`k1_off_gmyopic`, 40 site days, 12 workers).
The `rank_random` and `rank_popularity` pairs were at batch 8/40 after 55 minutes -- about 4.5 h per
unit -- while `rank_cartlabor`/`rank_minlabor`, the unpriced cells' slowest, were at 24/40 and
`fifo` had finished in 18.6 min. Unpriced (cell 1) the same two pairs took 8-12 min. So the priced
multiplier on these two families is ~25x, not the 3.2x the 10-batch ladder measured on
`uni_rank_labor_norsl`.

## Mechanism

`_RankedAssignPool.__init__` (and two sibling pools) compute

    self._all_idx = set().union(*aisle_idx_sets.values())

at every pool open. Under the gain evaluator the pool is opened over a copy-on-write VIEW of
`aisle_idx_sets`, and `_CowView.values()` materializes EVERY aisle (a copy of each set) so the
caller cannot mutate the live one -- `Inbound/gain_cow.py` says so in as many words: "`rank_random`
(its only phase-2 user) gets correctness and no win". A priced drain opens T(T+1) pools, and at
campaign scale the site has 2,774 aisles, so each open copies 2,774 sets before scoring anything.
The other pool families read by key and pay for the one or two aisles they touch.

## What to build

The union of every aisle's index set is the set of PLACED matrix indices, which the ledger now
carries as the key set of `partner_aisles` (the inverse of `idx_sets`, `8a3475ef`) -- but a
copy-on-write view carries no inverse by design, and the virtual placement's union must include
the overlay's additions. Two shapes, both to be measured before choosing:

1. `_CowSets.union()` -- the live union (from the owner's inverse, or computed once per drain
   and held on the frozen `SpaceView`, which is arm-local and drain-scoped, so legal) plus the
   overlay's sets, with no per-aisle materialization. Byte-identity is NOT free here:
   `rank_random` draws from `_all_idx` with an RNG, so the set's ITERATION ORDER is part of the
   arithmetic and a differently-built set can iterate differently. The toy run + `run_digest`
   is the gate, and it must include a `rank_random` arm under a priced cell.
2. Ask whether `_all_idx` is needed at pool-open at all for these families, or only lazily.

Not touched during the campaign: the run imports an immutable snapshot, and a change here moves
every priced cell's cost, which is the quantity the campaign publishes.

## Answer -- landed 2026-09-18 (campaign-scale re-measurement pending)

**The mechanism holds for `rank_random` and NOT for `rank_popularity`, by reading the code
and then measuring it.** `_RankedAssignPool.__init__` builds the union only when
`order_key is None`; `rank_popularity` orders by `_score_expected_popularity` and never
builds it, `rank_random` (no order key, a random aisle selector) does. So whatever makes
`rank_popularity` 25x priced, it is not this union. Profiled under `gain_myopic` at 20k SKUs
(uncoupled, 4 batches, 98 aisles): the union path (`_CowSets.values()` materialization +
`set().union`) was ~15% of a `rank_random` pool open, `_D_map` over the tier's candidates
~30%, the `by_aisle` sort key ~15%; at 2,774 aisles the union term scales with the warehouse
while the others scale with the tier. The campaign-scale share is being measured (200k SKUs
of the 400k catalogue, cProfile) and the ladder (`calltree_inbound_ladder.py --coupled`)
will record the new multiples.

**Campaign scale, BEFORE the fix (measured 2026-09-18/19, `uni_rank_random_norsl`, 200k SKUs
of the 400k catalogue, 10 batches, uncoupled, `gain_myopic`, cProfile):** wall 856 s; the
drain (`plan_order`) 376 s over 18 drains at depths 3-7, 402 `place_load`s, **7,384 pool
opens** (18.4 per place_load, 4,069 candidates each); `_make_pool` 287 s = 76% of the drain;
inside `_RankedAssignPool.__init__` (293 s): `set.union` **109.5 s** + `_CowSets.values()`
materialization **96.1 s** (4.67 M `__getitem__`, one per aisle per open) = **205 s, 70% of
pool construction and 55% of the whole priced drain**; `_D_map` 33 s; the `by_aisle` sort key
9 s. So at the campaign's aisle count the union IS the dominant term of a priced
`rank_random` open, as the ticket said -- and it is gone from the open now.  The AFTER
profile of the same run: **wall 856 s -> 437 s; the drain 376 s -> 72.4 s (5.2x); `_make_pool`
287 s -> 49.4 s (5.8x)**, same 18 drains, same depths, same 7,384 opens. The union term is
~3 s. What is left of a priced `rank_random` open is the tier -- `_D_map` over ~4,000
candidates and the `by_aisle` bucketing -- which scales with the tier, not the warehouse.
Memory: `priced-rank-random-cost-was-the-cow-union`.

**Built (shape 1 of the two proposed, made exact):**

- `AisleLedger.partner_aisles` is now `_PartnerAisles`, the inverse that COUNTS its placed
  keys (`n_placed`), mirrored at the same three write points as the inverse and checked by
  `reconcile()`. A plain dict handed to `over(partner_aisles=...)` is refused.
- `_CowSets.union()` answers `set().union(*self.values())` as a `_PlacedUnion` over the
  owner's inverse: `len` from the counter (EXACT, because `_delta_lift_from_row` folds the
  shorter side and a wrong size changes the float), `in` from the inverse, iteration from
  it, plus the overlay reconciled exactly (extra / gone) rather than assumed empty. No aisle
  is materialized. A live dict without an inverse gets the old materializing answer.
- `Assignment_Functions._placed_union` dispatches: the owner's dict keeps the historical
  set (the wave path is untouched, byte for byte); the evaluator's view takes `union()`. All
  three constructors (`_CoDemandPool`, `_RankedAssignPool`, `_ClusterMapPool`) go through it,
  and a test asserts no fourth `set().union(*aisle_idx_sets.values())` reappears.

**Byte identity, measured.** New spec `_toy_priced` (two cells off the phase-2 axis, `fifo`
and `gmyopic`, over `fifo` / `rank_random` / `rank_popularity`, coupled, the era): baseline
`comparison_whatif_20260918_234513` from the HEAD snapshot (`45bafa7a`) vs candidate
`comparison_whatif_20260918_234957` from the working tree -- `run_digest.py`: **IDENTICAL on
the comparable surface, 24 arms**. And the path RAN: instrumented at the same scale, 152
evaluator pool opens took the `_PlacedUnion`, 38 wave opens took the set.

The iteration-order residual (the lazy view iterates dict order, a set iterates hash order)
can only reach the arithmetic when the placed set is smaller than a SKU's partner row --
never for a stocked warehouse; recorded in `_PlacedUnion`'s docstring.

**Shape 2 ("is `_all_idx` needed at open at all") stays open**: with shape 1 the open no
longer walks the warehouse, so the question has lost its cost; what remains of the priced
multiple is in the tier (`_D_map`, bucketing) and in `rank_popularity`'s unmeasured term.
