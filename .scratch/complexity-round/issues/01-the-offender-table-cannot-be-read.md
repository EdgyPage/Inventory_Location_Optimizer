# The offender table ranks by exponent alone, and its top entry is timer noise

Type: task
Status: resolved

`calltree_growth.py` is "the O(n^2)-hidden-at-small-scale detector", and the offender table is
what a session reads before deciding what to refactor. Two defects made that table misleading.

## Defect 1 -- the top offender was noise, and it outranked everything true

The deep ladder's archived artifact
(`growth__knob-skus_ladder-deep_seed-42__20260820T231003Z_2cdea4386ab7.json`) has exactly one
offender: **`t_task` at k = 3.916, r2 = 0.946** -- a textbook super-linear exponent at a
convincing fit, sitting at the top of the table.

Its walls are `[4.9e-05, 3.9e-04, 0.0356, 0.0927]` seconds. **The fit rests on fifty
microseconds.** The whole section is worth 93 ms at the top rung.

The existing gate cannot catch this. `if max(ys) < 0.01: continue` asks whether the section ever
got BIG; what poisons a log-log fit is the SMALLEST positive point, because that is the end of
the lever arm.

Note for anyone re-deriving this: the report's rendered `walls` field rounds the first rung to
`0.0`, and a fit on the ROUNDED series gives k=3.928 at r2=0.877 -- which the r2 gate would drop
for a different reason entirely. Cite the ladder, not its display table. The test says so too,
because the first version of it made exactly that mistake.

## Defect 2 -- k answers "how fast", not "what should I fix"

`report['offenders'].sort(key=lambda o: -o['exponent'])` put section seconds, call counts,
per-placement ratios, arm totals and knees in ONE order keyed on a number that does not mean the
same thing in any two of them. A knee's "exponent" is a local step between two rungs; a section's
is a fitted wall; a function's is a call count.

That contradicts this project's own stated lesson, from the effort that produced this tool:

> element counts must be weighted by COST CLASS before ranking
> -- `docs/design/INBOUND_PERF_FINDINGS.md`

## What landed

* **`MIN_WALL_S = 0.005`** -- the smallest POSITIVE wall a fit may rest on. A fit that would have
  flagged but rests below it goes to a new `report['suppressed']` **with its reason**, never
  silently dropped: "too small to fit here" is a finding, and a vanished finding is how this
  repo has lost things before.
* **`_project(ys, k)`** -- the series' last value carried `PROJECT_FACTOR` (10x) further along
  the ladder. No refit needed: `y(10x) = y(x) * 10^k`.
* **`_severity_sort`** -- group by COST CLASS (seconds, then counts, then ratios, then knees),
  then by projected magnitude, then by exponent. Seconds are a cost; a call count is a proxy for
  one; a ratio is a shape; a knee is a warning that the fit does not hold at all. Ranking ACROSS
  those by one number would invent a common unit that does not exist.

## What it is worth -- measured, and smaller than it first looked

Replaying the archived artifacts through the new code:

* **The deep ladder's offender table becomes EMPTY**, with `t_task` in `suppressed` and its
  reason attached. Its only offender was noise.
* **On the meso `split_staging4` ladder the re-ranking is modest**: 4 of the old top 5 stay in
  the new top 5. It promotes `Aisle.Bin.location` (5,453,719 calls, k=1.45) from rank 9 to
  rank 5, and puts the 9.4 M-call item first instead of third.

That second number is deliberately stated small. Re-fitting the archived LADDER with today's code
produces 10 offenders where the archived REPORT listed 5, and it is tempting to read that gap as
the re-ranking surfacing buried findings. It is not -- it is fitter drift between 2026-08-26 and
HEAD. The ranking change alone moves one entry from 9th to 5th. `[a count is not a claim]`.

## Acceptance

Four tests in `Tests/calltree/test_calltree_smoke.py`:

* the `t_task` series (RAW values, asserted to still reproduce k=3.916 at r2>=MIN_R2) is
  suppressed rather than flagged, and the reason names the anchor;
* a clean k=2 fit anchored on a whole second STILL flags -- a suppressor that suppresses
  everything is the same defect facing the other way;
* a 5.4 M-call k=1.45 offender outranks a 377 k-call k=1.53 one;
* seconds outrank calls, and a knee with a local k of 9.9 outranks neither.

**Proven non-vacuous**: with `MIN_WALL_S` forced to 0 and `_severity_sort` replaced by the old
`-exponent` sort, all three checks fail with their own messages.
