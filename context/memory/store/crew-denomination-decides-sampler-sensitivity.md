---
name: crew-denomination-decides-sampler-sensitivity
description: a crew denominated in declared UNITS is invariant to the batch sampler; one denominated in LINES or PACKS is not — picking held bit-identical across the v2→v3 flip while put-away went 60→64 and receiving 22→23
metadata: 
  node_type: memory
  type: project
  originSessionId: d00ae947-94f7-4253-8a31-a99472d9be15
  modified: 2026-09-12T21:15:38.994Z
---

Measured 2026-09-12 (dept-cal 46) across the v2 -> v3 sampler flip on the reference pair, which
is a perfectly controlled comparison: same catalogue, same seeds, same config, only the batch
CONTENT differs.

**Bit-identical across the flip** -- because `n` is DECLARED (`mean_fraction` x section size, a
config value; the run log says `the fixed point is the declaration`), not measured from the
sampler:

- store 588.7 and fulfillment 2,903.2 lines/day; line floors 1.3078 / 1.4994
- sum Q 3,086,462 / 2,595,593; **2774 aisles / 2,505,050 bins**, expected_fill 89.3%
- the PICKING crew K=23, `s_pick` 17.657 (ff) / 105.130 (store) s/unit
- bins filled 84.1% store, 94.1% fulfillment

**Moved with the flip:**

| | v2 | v3 |
|---|---|---|
| put crew | 60 | **64** (load 1,448,387 -> 1,547,546 s/day, +6.8%) |
| receiving crew | 22 | **23** (15,645.5 -> 17,105.1 packs/day, +9.3%) |

**Why:** picking is denominated in demanded UNITS, which the era declares and the sampler cannot
touch. Put-away and receiving are denominated in the replenishment each delivered LINE triggers,
and v3 delivers the 9.5% more fulfillment lines that v2 was swallowing
([[v2-sampler-redraws-selected-skus]]). The +9.3% in packs/day tracks the +9.46% in delivered
lines almost exactly, which is what identifies the mechanism rather than merely correlating with
it.

**How to apply:** before attributing a crew-size change to a policy, a catalogue or a code change,
ask what the crew is denominated in. A units-denominated crew moving means the DECLARATION moved;
a lines- or packs-denominated crew moving can be nothing more than the generator delivering a
different number of distinct lines for the same declared demand. It also means a sampler or
generator change is NOT automatically a geometry change -- check, do not assume, which dept-cal
46 got wrong in its own ticket body before the run corrected it. Related:
[[v3-sampler-era]], [[receiving-is-its-own-crew]], [[a-count-is-not-a-claim]],
[[config-knob-has-five-seams]].
