# The HEAD offender table

Type: research
Status: claimed

**No archived artifact may be cited as a HEAD number, and this ticket exists because of that.**

`Tests/calltree/out/index.json` holds 38 artifacts. Every one predates `bd29d2eb` -- the `take`
heap that removed the O(A) per-placement scan from `_TravelBalancedPool` -- so the single most
prominent name in the archive (`_aisle_best` / `_score_of` at k = 1.55) describes code that has
since changed shape. Two further reasons nothing in there is quotable:

* the `yard`-knob artifact's x axis is `[2.2938, 2.2938, 2.2938, 2.3723, 2.3723]` -- a 4% range
  with duplicates, the mis-calibration `.scratch/inbound-performance/issues/05` retracted;
* every `t_sample` exponent describes the `v1` sampler, retired 2026-09-12 (ticket 02).

## Preconditions, and why each is checked rather than assumed

1. **The instrument is green on HEAD.** `python -m pytest Tests/calltree -q` -> **26 passed,
   exit 0, 456 s**. This is not ceremony: the tier is in no CI gate, and it was
   1-failed/21-passed on clean `develop` once before, from a frozen oracle re-priced in
   production and not in the test tier.
2. **The offender table is readable** -- ticket 01.
3. **The fixture runs production's configuration** -- ticket 02.
4. **A quiet host.** Four samples of identical work once gave run differences of +19.7, +24.0,
   -0.6 and +1.4 s. The phases below run sequentially for this reason.

## What is being measured

    python -m pytest Tests/calltree -q                                          # re-confirm green
    python Tests/calltree/calltree_growth.py --ladder meso --knob skus
    python Tests/calltree/calltree_growth.py --ladder meso --knob skus --config split_staging4

`--config` matters and is not an optional extra: until 2026-08 no ladder set `put_timing`,
`put_split`, `put_staging` or `recv_crew`, so `_admit_held`, `_held` and `HeldItems` were
**structurally dead in every rung** and a quadratic living there was reported as clean.

## Checks before any exponent is read

Recorded here so the numbers cannot be read past them:

* **the x axis actually moved** -- a 4% range is noise dressed as a law;
* **intermediate counts differ** between rungs. A ladder whose rungs exceed its fixture's declared
  size runs the SAME catalogue and prints different ratios with no warning;
* **`flows: ALL ZERO` means NOT MEASURED**, not "the path never ran". A level read at the end of a
  run is not coverage; a flow is.

## Answer

(pending -- the run is in flight)
