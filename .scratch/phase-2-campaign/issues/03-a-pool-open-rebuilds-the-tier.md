# 03 - every pool open under the evaluator rebuilt the whole tier to seat a dozen units

Type: task
Status: resolved

**Seen 2026-09-19** on the relaunched campaign (`inbound_unload`, root
`comparison_whatif_20260919_002111`): the priced cell's winner-pair units ran at ~3 h against
25-40 min unpriced, ~4.5x, flat across batch windows. Profiled at campaign scale
(`uni_rank_random_norsl` and `uni_rank_cartlabor_norsl`, 200k SKUs, 10 batches, `gain_myopic`,
after ticket 02): a drain of 18 plans made ~400 virtual load placements and ~7,400 pool opens;
each open took a tier of ~4,100 empty bins, built the pool's whole ranked structure over it
(the travel-cost map, the per-aisle per-bracket D-sorted buckets, the aisle heads), seated
~12 units, and threw it away. `_TravelBalancedPool.__init__` was 45 s of a 65 s drain (29 s in
its per-candidate loop alone); `_RankedAssignPool.__init__` 54 s of 72 s. The build is O(bins)
per open; the use is O(units placed); the ratio is ~300.

## Mechanism

The gain evaluator freezes its space view at the drain epoch, so a tier's empties are the
same list at every one of the T(T+1) x K opens of one drain, and a bin's travel cost is
geometry. The only thing that differs between two opens of the same tier is which bins the
current virtual placement has already taken. The pools could not know that: each was handed
a freshly filtered list and had no structure to reuse.

## What was built (the user's three parts, 2026-09-19)

1. **Arm the live aisle index for the ranked families** -- NOT taken, and the reason is the
   tie-break. The manager's `_aisle_index` orders equal-D bins by insertion time; the pools
   order them by first appearance in the frozen candidate list, and that order is a real
   tie-break in every pool (`_rank`, `next(iter(head_bin))`, the strict `<` over brackets).
   A live index cannot carry the snapshot's appearance rank, so a pool opened over it would
   not be byte-identical on ties -- and the per-drain sort it would save is ~7-8 tiers of
   ~4,000 bins per drain, milliseconds against a 65 s drain.
2. **Freeze once per drain.** `Warehouse/placement/frozen_tier.py:FrozenTier` -- the tier's
   candidates in appearance order, every per-aisle and per-(aisle, bracket) bucket stable-
   sorted by D once, plus the D map. Owned by the evaluator (`_Evaluator._tiers`, keyed
   `(key, predicted)`), built on first touch through the bundle's `freeze_tier` (handed over
   by the driver like `wp_of`, because `Inbound` may not import the placement engine).
3. **A pool open is an overlay.** `TierSlice` = the frozen tier plus one exclusion set;
   each bucket is a `_Cursor` that skips excluded bins lazily and speaks the deque protocol
   the pools already read, so `take` is one code path. Aisle and bracket order are recomputed
   per open from each list's first non-excluded appearance index (O(aisles + skips)) --
   excluding an aisle's first bin can move it behind another, and the eager build would have
   had it there. All three pool classes (`_RankedAssignPool`, `_TravelBalancedPool`,
   `_MinLaborPool`) accept a slice where they accept a list; handed a list they build exactly
   as before, so the wave path is untouched byte for byte.

## Evidence

- `Tests/unit/test_frozen_tier.py`: 54 exact cases (float `==`) -- each pool driven eager vs
  sliced over deep-copied state, seeded candidate lists with planted D ties across aisles and
  exclusions that take an aisle's first bin, cart on/off, minimise/maximise, plus the slice's
  own arithmetic and the paces/brackets refusal. In the CLAUDE.md gate.
- Toy digest: `_toy_priced` candidate `comparison_whatif_20260919_090929` vs the pre-ticket-02
  baseline `comparison_whatif_20260918_234513`: **IDENTICAL on 24 arms**; 152 of 152 evaluator
  opens took the slice (both families), 76 tiers frozen over 10 drains.
- Campaign scale, `uni_rank_cartlabor_norsl` (200k SKUs of the 400k catalogue, 10 batches,
  `gain_myopic`, uncoupled, cProfile; the AFTER run shared the machine with the 12-worker
  probe, so its wall is contended and the drain-internal split is the number):

  | | before | after |
  |---|---|---|
  | drain (`plan_order`, 18 drains, depths 3-7) | 64.7 s | 37.9 s |
  | `_make_pool` (7,444 opens) | 44.3 s | 3.2 s |
  | `_TravelBalancedPool.__init__` | 45.2 s (29.3 s self) | 5.2 s |
  | tier freezes (`_tier_for`, once per key per drain) | -- | 7.3 s |
  | `take` (82,290 units) | 9.4 s | 24.1 s (contended; `_aisle_best` 13.0 s) |

  The open itself went from 6 ms to 0.4 ms. What is left of the drain is placing the
  units (`take`, and `_aisle_best`'s O(aisles) rebuild at every SKU-run boundary) and
  the pricing -- both proportional to units, not to the tier.
## Second and third cuts (2026-09-19, same day)

Coupled campaign-scale profiles (six days, `gain_myopic`, 200k SKUs, uncontended except where
noted), drain = `plan_order` over 10 drains at depths 4-10:

| pairing | before | after cut 2 | after cut 3 |
|---|---|---|---|
| cartlabor / cartlabor | 213 s (`take` 202, `_aisle_best` 101) | 137 s (`take` 121, `_aisle_best` 36) | -- |
| minlabor / minlabor | 634 s (`take` self 250, centroid 119, `_aisle_best_cost` 104) | -- | 346 s (`take` self 51, centroid 85, `_aisle_best_cost` 50, `_partner_deltas` 39) |

What is left in the min-labor drain is mostly the policy's own per-unit work -- the centroid's
fold over the winning aisle's members (85 s, order-bearing so not invertible) and the
run-boundary scan -- plus one admin cost still worth a cut: `_CowListsByKey.__getitem__`
materialises an aisle's WHOLE `member_pos` mapping (every SKU's list) the first time a pool
touches it, 768k times for 71 s. A finer-grained view that copies only the appended list,
yielding live keys in live order then new keys, would be byte-identical for the centroid's fold.


**The probe said the open was not the campaign's cost.** Its priced cell paced no faster than
the stopped run's (window 1: 799 s of drain vs 843 s; window 2 not faster at all), and a
COUPLED profile of the winner pair at campaign scale (six days, `gain_myopic`) showed why: with
the open gone, `_TravelBalancedPool.take` was 202 s of a 213 s drain -- 29 M `_aisle_best` +
`_score_of` evaluations at SKU-run boundaries (a load carries mostly distinct SKUs, so nearly
every unit opens a run and touches every live aisle) -- and the first overlay had made each of
those reads DEARER (two cursor settles per bucket per read, 57 M `_settle_lo` calls) than the
plain list index it replaced. The uncoupled store-only profile had hidden this because the
store leaf places 2.8k units a day against the fulfillment leaf's 18k.

**Second cut** (`d1c02277`): a bucket's head is a plain `head` attribute on both bucket kinds,
settled at construction and after its own pop (the exclusion set is fixed for a pool's life);
`_aisle_best` reads it and memoises `per_pick` per height multiplier for the run's var;
`_score_of` is inlined in the run-boundary loop, one `_aisle_best` call per live aisle kept
because the scan detector counts it. Coupled cartlabor drain 213 s -> 137 s.

**Third cut** (this commit): the fulfillment side. `_MinLaborPool.take` was 649 s of a 634 s
coupled drain on the min-labor pairing: the SKU's partner row rebuilt from the CSR on every
unit (119 s), `per_pick` per aisle per bracket at every run boundary (26 M calls, 104 s), and
the per-aisle affinity delta folded aisle by aisle inside the prune loop (most of the 250 s of
`take`'s own time). Now, per SKU run: `per_pick` memoised per multiplier; the row built once
(`_partner_row`) and handed to the centroid; and the deltas folded ONCE through the ledger's
inverse (`_partner_deltas`, the `_co_by_aisle` argument: row order per aisle, so the same
left fold from 0.0), with a copy-on-write view's overridden aisles folded the old way over the
view's own set. Within a run only the SKU's own index moves, which is no partner of itself,
so the fold holds for the run. Pinned three ways in `test_frozen_tier.py`: plain dicts (the
per-aisle fold), the owner ledger (the inverse), and a view with an aisle overridden before
the run -- eager and sliced, exact -- plus the fold's arithmetic term for term.

- Toy digest after the third cut, on the WIDENED `_toy_priced` (five families, 20 units):
  baseline `comparison_whatif_20260919_122111` from a snapshot of `231d4f9d` (the last
  placement code before this ticket) vs candidate `comparison_whatif_20260919_122139` from
  HEAD `38bc098d`: **IDENTICAL on 40 arms**.
- The first probe (`comparison_whatif_20260919_093001`, snapshot `a58f67c2`) was stopped
  after its fifo cell digested IDENTICAL (8 arms) against the stopped campaign's cell 1 --
  the priced cell was pacing no faster on that cut. Relaunched from `38bc098d`.
- **Fourth cut** (`8fda0d12`, `dfcaca55`): `_CowListsByKey` hands out a `_CowInner` per aisle
  that copies one list on a write instead of the aisle's whole member map (768k copies, 71 s).
  The first draft read through a Python generator and made the centroid three times dearer;
  the second reads the live dict's own items for an unwritten aisle and merges once per new
  key for a written one (14 s of merges on the same unit). Its campaign-scale profile ran
  contended (the probe's four units alongside), so its drain is not comparable; the copy is
  gone from the table and everything else scaled by the contention. Toy digest IDENTICAL on
  40 arms, twice.
- Probe relaunch from `38bc098d` (root `comparison_whatif_20260919_123309`): cell 1's winner
  units took 19 min against 27 on the stopped run -- the WAVE path's gain from cut 3.
- **The priced cell at campaign scale, the number that matters** (probe cell `k1_off_gmyopic`,
  12 workers, 4 units, against the stopped run's cell 3 -- same seeds, same `dur` per batch):

  | unit | window | stopped run (reord / wall) | probe (reord / wall) | speed-up |
  |---|---|---|---|---|
  | uni cartlabor+minlabor | batches 1-4 | 843 s / 1,022 s | 315 s / 488 s | 2.7x / 2.1x |
  | uni cartlabor+minlabor | batches 5-8 | 2,386 s / 2,404 s | 1,109 s / 1,127 s | 2.2x / 2.1x |
  | opt cartlabor+minlabor | batches 5-8 | 1,275 s | 624 s | 2.0x |

  The stopped run's uni unit took 6 h 05 min end to end (02:14 -> 08:19); at this pace the
  probe's takes ~2.9 h, so a priced cell is ~3 h and the 40-unit campaign ~22 h at 12 workers,
  against the ~2.5 days it was heading for. The unpriced cell's winner units went 27 -> 19 min.
- **The cell-level digest landed (2026-09-19 16:34).** `run_digest.py --cell k1_off_gmyopic`
  of the relaunched probe (`comparison_whatif_20260919_123309`, snapshot `38bc098d`) against the
  stopped campaign root (`comparison_whatif_20260919_002111`): **IDENTICAL on the comparable
  surface, 8 arms** -- the priced cell at 200k SKUs, 40 site days, every family the winner pair
  and the rider exercise. With the fifo cell IDENTICAL on its 8 arms earlier in the day, the four
  cuts are byte-identical at campaign scale, not only on the toy. The probe exited 0 and its
  task is unregistered.

  The full-unit walls of that cell, stopped run vs probe (same seeds, 12 workers both):

  | unit | stopped run | probe | speed-up |
  |---|---|---|---|
  | opt fifo rider (store / fulfillment) | 806 s / 700 s | 788 s / 683 s | 1.0x |
  | uni fifo rider (store / fulfillment) | 809 s / 700 s | 798 s / 690 s | 1.0x |
  | opt cartlabor / minlabor | 16,898 s / 15,000 s | 7,803 s / 7,254 s | 2.17x / 2.07x |
  | uni cartlabor / minlabor | 21,773 s / 21,653 s | 10,449 s / 10,333 s | 2.08x / 2.10x |

  The priced cell is bounded by its slowest unit: 6 h 03 min -> 2 h 54 min. The rider is
  untouched, as a strict no-op should be. This closes the ticket; what remains of the priced
  unit's cost is `take`'s per-SKU-run scan over live aisles, which is a scan WIDTH question
  (`AisleHeadIndex`, plan item 3.6) and not a rebuild.
