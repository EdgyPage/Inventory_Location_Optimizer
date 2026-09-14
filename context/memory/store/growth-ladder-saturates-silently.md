---
name: growth-ladder-saturates-silently
description: "a growth ladder whose rungs exceed its fixture's declared size reports identical runs as three different ratios, with no error and no warning"
metadata: 
  node_type: memory
  type: project
  originSessionId: 07c0329c-5d0a-4525-8fb1-528b0979b765
  modified: 2026-09-14T14:22:12.360Z
---

`run_fullfid` (inbound calltree ladder) took `pairs[0]` of `find_latest_db_pairs` — the LATEST
profile run — whose catalogue declared 40,000 SKUs, while 150,000- and 400,000-SKU catalogues sat
unused beside it. Rungs requested at 40k/60k/80k SKUs all silently ran the SAME 40k catalogue and
produced three IDENTICAL runs: same pool count (1,282), same yard depth (2.25), same max depth
(3), drain within 3% — and the tool printed them as three rungs with three different ratios.
`--max-skus` above the catalogue's own declaration is not an error and not a warning; it just
takes everything the catalogue has.

**A growth ladder that saturates its own knob reports "flat" and looks identical to a subsystem
that does not grow.** This is exactly how [[inbound-pool-adapter-multiplier-is-not-13x]]'s
original ~1.1x reading happened — a ladder topping out at 20k SKUs never moved the yard depth T
far enough to see the cubic drain cost. Identical counts (pools/depth/entries) at different rung
sizes is the signature that catches it.

**Inverse of [[a-bin-cap-is-self-defeating]]:** there a cap BELOW the declaration refuses loudly;
here a declaration ABOVE the fixture is truncated in silence.

**The fix, as landed:** `run_fullfid(min_catalogue=N)` binds the newest catalogue DECLARING at
least N SKUs, read from `run_metadata.params_json['num_skus']` — not a row count, so a truncated
table cannot agree with itself. The ladder sizes that floor on its TOP rung so one catalogue
serves every rung, and a rung still above its catalogue's declaration prints SATURATED rather than
silently repeating. Binding a catalogue per-rung would be worse than the bug: the available
catalogues are a month apart in generator vintage (40k is 2026-09-13, 400k is 2026-08-16), and a
per-rung bind would report vintage drift as growth.

**Why:** a ladder is only informative if the knob it varies actually reaches the subsystem under
test; a saturated knob and a genuinely flat subsystem are indistinguishable from the ratios alone.

**How to apply:** before reading any growth ladder as flat, confirm the knob moved the thing it
drives — check that the intermediate counts differ between rungs, not just the requested input.
Full record: `docs/design/INBOUND_PERF_FINDINGS.md`, `.scratch/inbound-performance/issues/
13-*.md`, `.scratch/inbound-performance/map.md`.
