---
name: pool-tier-loop-cost-class-before-count
description: "_make_pool's cost is the TIER loop (BinKey groups), not the candidate loop; element counts must be weighted by cost class before ranking a refactor target"
metadata:
  node_type: memory
  type: project
---

`_make_pool` (`Warehouse/placement/Assignment_Functions.py`) opens 7,426 times in a measured
25-batch, 2,000-SKU run: 48 `plan_order` entries × 12.3 `place_load` calls/entry × 12.59 pool opens
per `place_load` — and that 12.59 is **BinKey GROUPS per `place_load`**, not spill-chain tiers
(tier iterations == `_place_pool` calls == `_make_pool` calls, exactly, with zero unseated units;
the spill chain averages 2.84 tiers and only its first entry is ever used). A group is 12.70 units
mean, median **3** — not the ~160 units/call that number actually measures units-per-`place_load`,
spread over the 12.59 groups.

**Element counts must be weighted by COST CLASS before ranking a refactor target.** The receive
drain splits: pool `__init__` over the tier 59.0% (462 ns/candidate, 8,130,323 candidates), the
`excluded` filter 12.0% (93 ns/elem), the `live` rebuild 8.9% (70 ns/elem, **0% removed** — see
below), the take loop 8.9%, `pool.order`'s LPT sort 0.68%, `SpaceTimeline.freeze` 0.8% (2.28M
copies, but C-level `tuple()` at 21 ns each). The pool's elements cost **6.6x** the `excluded`
filter's and **22x** `freeze`'s per element — an earlier ranking pass in this same effort ranked a
candidate by raw element count and would have over-ranked `freeze` on exactly that mistake.

**Acquitted by measurement, do not re-convict:** `_place_merge`'s sort, `place_load`'s
`groups`/`avail_cache` rebuild, the `expect_heads` branch, `_window_rates`, and
`SpaceTimeline.freeze`.

**What this cost-class read enabled to land:** copy-on-write `AISLE_VIEWS` beside `AISLE_COPIERS`
in `Inbound/gain.py` (64-117x fewer element copies; drain -45.5%/-49.3%/-77.7% for
labor/cartlabor/minlabor — the last being fulfillment's #1 and store's #2 arm in `PHASE2_PAIRS`),
and a guard on the `live` rebuild (measured 8,130,323 scans, zero removals — `used` is non-empty
only when a later BinKey group spills into a tier the same placement already drew from, which
never happened in 7,426 opens; guarded rather than deleted, since "never happens" is a measurement
of today's spill behaviour, not an invariant the code declares).

**Why:** a raw scan/element count conflates cheap C-level operations with expensive Python-level
ones; ranking on count alone reliably over-convicts the cheap-but-frequent operation.

**How to apply:** before ranking a hot loop for refactor, measure ns/element (or ns/call) per
candidate section, not just call/element counts. Full record:
`docs/design/INBOUND_PERF_FINDINGS.md` §1-2. Related:
[[pool-candidate-slice-was-built-not-landed]], [[calltree-framework-first-findings]],
[[a-count-is-not-a-claim]].
