---
name: inbound-pool-adapter-multiplier-is-not-13x
description: "RETRACTED 2026-09-14: at campaign scale (400k SKUs) the pool-adapter RUN multiplier is 3.24x, not the ~1.1x this memory first reported; the method that found the error survives"
metadata: 
  node_type: memory
  type: project
  originSessionId: 07c0329c-5d0a-4525-8fb1-528b0979b765
  modified: 2026-09-14T14:21:40.290Z
---

**RETRACTED 2026-09-14.** The original headline — "a pool-adapter gain cell costs ~1.1x a whole
run, not 13-18x" — was measured on a ladder that topped out at 20k SKUs and read as flat. A
follow-up ladder to campaign scale (`Tests/calltree/calltree_inbound_ladder.py --coupled`, real
driver, coupled site, `uni_rank_labor_norsl`, one catalogue — `mixed_20260816_131535`, 400,000
SKUs, the campaign's own declared size) shows the RUN multiplier is **not flat**: 0.94 / 1.13 /
1.21 / 1.53 / **3.24** at 25k/50k/100k/200k/400k SKUs. The DRAIN multiplier keeps growing too
(9.83 → 349.07). At 400k the drain is 956s of a 1373s run — 70% of the whole run. **The worry
that chartered this effort — that phase 2's 8.6-9.7h sizing from `('fifo','tmin')` undercounts
the 8 pool-adapter arm-slots — is supported, the opposite of the original conclusion.**

Fitted over the 16x campaign-scale span (r2 in parens): drain seconds ~ T^3.13 (0.998, T = yard
depth), pools opened ~ T^1.67 (0.996), seconds-per-pool-open ~ T^1.46 (0.976); 1.67+1.46=3.13
exactly. `entries` (the count of drains) is constant at 18 across every rung — none of the growth
is more drains, all of it is cost per drain, and that cost is cubic in T. See
[[growth-ladder-saturates-silently]] for why the first ladder missed this.

**What SURVIVES from the original finding is the method, not the conclusion:** two multipliers
with different denominators (DRAIN vs. RUN), paired within a rung, with the commensurable one
named; never read a DRAIN ratio against a RUN-shaped campaign estimate. That discipline is still
correct and is how this retraction itself was reached — reading RUN against RUN at a scale that
actually moves T. What was wrong was concluding "flat, therefore does not grow" from a ladder
whose own driving variable (T) had stopped growing.

**Why:** a multiplier is only as good as the range it was fit over; a flat-looking RUN ratio at
5k-20k SKUs was a saturated knob, not a stable regime — see [[growth-ladder-saturates-silently]].

**How to apply:** before citing an inbound pool-adapter cost against a campaign estimate, confirm
the ladder's top rung reaches the campaign's own scale (T, not SKU count, is the driving
variable), and confirm the multiplier is still growing there before treating it as flat. Full
record: `docs/design/INBOUND_PERF_FINDINGS.md` §1 and §6 (retraction at the top),
`.scratch/inbound-performance/issues/12-*.md`, `13-*.md`, `.scratch/inbound-performance/map.md`
tickets 11-13. Related: [[inbound-optimization-map-closed]], [[a-count-is-not-a-claim]],
[[inbound-yard-is-a-stable-queue-under-the-era]].
