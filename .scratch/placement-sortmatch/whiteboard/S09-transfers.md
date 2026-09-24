# S09 -- extending the successful ideas to the other placement assignment functions

The user (2026-09-24, mid-plan): "Extend any successful optimization idea to any of the
placement assignment functions as well."  Two ideas succeeded:

1. **Exact frontiers instead of set filtering** (S01): a structure the placement already
   has -- sorted tiers consumed from one end -- read as integers instead of as sets.
2. **Two sorted lists sharing an index** (S02-S06): packs by work, bins by a travel-plus-
   height cost.  That one changes decisions, so it can only ship as a NEW arm
   (`rank_sortmatch`), never as an edit to an existing rule.

The transfer rule (plan): an EXACT transfer goes into the existing function behind that
family's oracles and an IDENTICAL digest; a decision-changing one ships as a new arm.

## Transfer A -- early stopping over sorted lists, into the two balance pools

The hot spot of both remaining pool families is a full scan of every live aisle at every
SKU-run boundary (`_TravelBalancedPool`'s rebuild, 72% of a campaign-shaped open;
`_MinLaborPool`'s best-bracket cost + sort).  The sorted-lists idea offers an exact
replacement IF the argmin can be found without looking at most aisles:

* travel-balanced: Fagin's threshold algorithm over K + 1 lists (aisles by load; per
  height bracket, aisles by head D).  Every term is monotone and the cart term is >= 0,
  so tau = load_cursor + fq min_m(pp_m + D_cursor,m) bounds every unseen aisle exactly.
* min-labor: its order is the K-way merge of per-bracket head lists offset by pp_m, so a
  lazy merge produces the walk's prefix without the O(A log A) sort.

**No prediction was registered** -- this was run as a feasibility check before building
anything; the bar it had to clear to be worth building was an early stop visiting well
under half the live aisles.

**Measured** (`assets/s09_ta_depth.py`, `assets/s09_walk_depth.py`; calltree meso,
2,400 SKUs, 6 batches, production placement drains):

| pool | boundaries / takes | live aisles (median) | aisles an exact early stop visits |
|---|---|---|---|
| travel-balanced (uni_rank_cartlabor) | 3,039 boundaries | 39 | mean **83%**, median 100% |
| min-labor (uni_rank_minlabor) | 79,420 takes | 41 | mean **75%**, median 95% of the walk |

**Refuted.**  The diagnosis is the aisle-churn S09 frontier law, seen from the other
side: the travel-balanced rule is WATER-FILLING -- it spends load until every aisle's
level is equal -- so the load list gives no aisle away, and the min-labor rule's affinity
reward is large against the spread of `fq bc`, which is why its prune rarely fires (the
pool's own comment records the 400k `uni_` case where it never does).  Both objectives are
built so that every aisle stays competitive; a bound that could skip most of them would
contradict the rule's purpose.  **Not implemented.**

## Transfer B -- sort-match as a rule, into the other families

Decision-changing, so as NEW arms only.  `rank_sortmatch` is the one built (S06).
`tmin` is the same machinery with travel-only keys; the lab measured the height-aware
bin key worth ~3 points of realised store work over it (S04), which is a reason to
PREFER the new arm, not to edit tmin.

## Transfer C -- frontier reads, into the pool families' gain pricing

The pool adapter opens over `TierSlice`s whose `_Cursor`s already skip excluded bins
lazily from the front of each (aisle, bracket) run (ticket 03 of the inbound-throughput
map), which is the pool-side form of the same frontier; the set-difference cost S01
removed for merge plans does not exist there in the same shape.  Nothing to transfer.

## What is left, and it is NOT one of the two ideas

Where the scan is inherent (both balance pools), the only lever left is its constant:
maintain the per-(aisle, bracket) head D as arrays once per pool open and evaluate the
boundary score vector in numpy (the same IEEE float64 operations in the same order, and
`argmin`'s first-occurrence rule is the rank tie-break).  That is a vectorisation, not
an algorithm, and it would re-pin `test_placement_selection_is_not_a_scan.py` -- a
decision for the user, recorded here as the open lead.
