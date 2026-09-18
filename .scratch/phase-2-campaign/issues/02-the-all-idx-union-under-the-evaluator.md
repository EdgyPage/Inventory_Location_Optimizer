# 02 - `_all_idx` is rebuilt from every aisle on every pool open, and the evaluator opens T(T+1) pools per drain

Type: task
Status: needs-triage

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
