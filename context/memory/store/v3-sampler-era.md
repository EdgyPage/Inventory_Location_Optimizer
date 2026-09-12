---
name: v3-sampler-era
description: "since 2026-09-12 the declared batch sampler is v3 (segment tree) — the sixth and widest comparability break, because it moves every batch sequence and with it the coverage fixed point, the line floor and the derived picking crew"
metadata: 
  node_type: memory
  type: project
  originSessionId: d00ae947-94f7-4253-8a31-a99472d9be15
  modified: 2026-09-12T18:04:44.532Z
---

`CONFIG['global']['sampler'] = 'v3'` since 2026-09-12 (`Optimization/config/settings.py`
`SAMPLER`), replacing the v2 default of 2026-08-20 ([[v2-sampler-era]]). v3 is
`_lift_weighted_sample_v3` over a `_SegTree` in `Warehouse/picking/Workload_Builder.py`; the
`BatchConfig.sampler` dataclass default stays `'v1'` for tests and diagnostics.

**Why it exists:** v2 re-drew SKUs it had already taken and the sku-keyed `Batch.items`
collapsed the repeats, so every v2 batch delivered fewer lines than the era declared
([[v2-sampler-redraws-selected-skus]]). v3 is the first version that delivers exactly `k`
distinct SKUs -- measured on the reference pair, 40 batches, **0/40 short on both channels**
(store 618.1/618.1, fulfillment 2,980.6/2,980.6 per batch) against v2's -1.02% and -8.64%.

**How it is built:** a segment tree whose every internal node is RECOMPUTED from its two
children instead of adjusted by a delta, so no residue can survive a removal (its root is
bit-for-bit a fresh rebuild of its own leaves -- the Fenwick's was 14.6% out). `find` descends
only into subtrees holding positive mass, which makes an already-drawn SKU **structurally**
unreachable rather than merely improbable. Its boundary rule is strict (`u >=` left goes right)
where v1's `searchsorted` is side='left'; they differ only on exact prefix boundaries, measure
zero for a continuous draw, and the strict form is what buys the guarantee.

**Cost:** 1.53x v2 on fulfillment, 1.10x on store (0.57 / 0.29 s per batch at 160k / 240k SKUs,
partner-map cache warm) -- still ~1/35th of v1's 21.6 s.

**This is the SIXTH comparability break** -- after the per-item charge `fc7a46a5`, the placement
pools `a033aff`, ADR-0003's drain order, the derived fill (dept-cal 35) and the lead-aware record
(dept-cal 37) -- **and the widest of the six**, because it moves every batch sequence rather than
one priced quantity. Absolute pick, travel, throughput and labour numbers do not cross
2026-09-12.

**What it does NOT move is the geometry.** This memory first predicted that the coverage fixed
point, the line floor and the derived picking crew would move with it. They do not: `n` is
DECLARED (`mean_fraction` x section size), so the floors (1.3078 / 1.4994), the levels
(3,086,462 / 2,595,593), the warehouse (2774 aisles / 2,505,050 bins) and the picking crew
(K = 23) came back bit-identical across the flip. The put-away and receiving crews did move
(60 -> 64, 22 -> 23) because they are denominated in lines and packs
([[crew-denomination-decides-sampler-sensitivity]]). That makes the flip a controlled
experiment: same warehouse, same crew, only the batch content differs.

**The era is CALIBRATED again under it.** The reference pair was re-run (dept-cal 46,
`comparison_20260912_134002`) and the equilibrium instrument reads 12 arms judged, 0 failed;
dept-cal 43 closed the calibration on that run rather than taking a second one. Batch caches are
fingerprinted per sampler, so no v3 run can be served a v2 file.

**A collapsed batch now refuses.** `Batch.__init__` raises when `len(items) != len(selected)`
under any sampler that promises distinct draws (v1, v3); v2 is exempt on purpose so its archive
stays reproducible. The check is against `selected`, never `k` -- a short draw is legitimate when
the live weight runs out, and only the collapse is a bug.

**How to apply:** before comparing two runs, read each one's `run_spec.json` `sampler` -- three
eras now exist and mixing their rows is silent. Absolute pick, travel, throughput and labour
numbers published before 2026-09-12 were drawn under a sampler that under-delivered fulfillment
lines by 8.6%, so they are not comparable with anything drawn after. Recorded as ADR-0006 (`docs/adr/0006-the-fill-law-gap-was-a-sampler-artifact.md`), which
carries the gap this flip closed and the fitted multiplier that was rejected with it. Related:
[[per-item-charge-hard-break]], [[derived-fill-is-the-fourth-comparability-break]],
[[lead-aware-record-is-the-fifth-comparability-break]],
[[v2-defect-manufactured-the-fill-law-evidence]],
[[crew-denomination-decides-sampler-sensitivity]].
