# 03 - every pool open under the evaluator rebuilt the whole tier to seat a dozen units

Type: task
Status: in-progress

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
- Campaign scale: the AFTER profiles and the cell-level digest against the stopped run's
  finished cells are recorded below when they land.
