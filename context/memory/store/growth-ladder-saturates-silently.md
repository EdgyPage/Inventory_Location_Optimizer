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

**The DEEP tier had three more of these, all found by running it 2026-09-16, and all now fixed:**

- **Its rungs could not run at all.** Each carried `--s-max-bins`/`--ff-max-bins`, and those bind
  BELOW the era's declared stock levels, so the planner refuses (`UnfieldableRequirement`). "No
  cap value would have worked" -- a smaller warehouse raises lines/day, which grows the levels.
  Rungs now shrink with `--coverage-days`, which lowers the DECLARATION.
- **It reported a clean bill after every rung failed** -- "no super-linear offenders flagged" plus
  an archived JSON, because `RUNG FAILED ... continue` left nothing downstream able to tell
  "measured and clean" from "never ran". It now refuses to report below three surviving rungs and
  records `failed_rungs` in the artifact.
- **It bound the NEWEST catalogue, not one big enough.** `find_latest_db_pairs` is
  `ProfileTree.latest()`, so rungs above the bound catalogue truncated in silence. `run_simulation
  --profile-run NAME` now names a run (new 2026-09-16), and the ladder sizes itself to the
  catalogue, drops what it cannot serve BY NAME, and prints the surviving span.

**So a clean deep-tier artifact in `out/archive/` from before 2026-09-16 may mean the tier never
ran.** Check `failed_rungs` and the printed span before citing one.

**How to apply:** before reading any growth ladder as flat, confirm the knob moved the thing it
drives — check that the intermediate counts differ between rungs, not just the requested input.
Full record: `docs/design/INBOUND_PERF_FINDINGS.md`, `.scratch/inbound-performance/issues/
13-*.md`, `.scratch/inbound-performance/map.md`.

**Third instance in one effort (2026-09-14):** [[pool-candidate-slice-was-built-not-landed]]'s
original rejection was also correct only over its tested range (600-6,000 SKUs) and read as a
standing conclusion; a campaign-scale re-test found three of its premises scale-dependent. Same
shape as this memory and the retracted [[inbound-pool-adapter-multiplier-is-not-13x]]: a finding
is not wrong, the range it was measured over was never stated as a boundary.
