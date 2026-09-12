---
name: coupled-runs-are-byte-identical-until-the-pool
description: "the window is CLOSED — since 2026-09-11 a coupled run fields ONE site put crew instead of one per leaf, so coupled and uncoupled put/travel numbers are no longer comparable; a zero difference is now the bug, not the feature"
metadata: 
  node_type: memory
  type: project
  originSessionId: 68469de0-3988-452f-9eb3-bc8fb5e0c145
  modified: 2026-09-12T00:56:56.426Z
---

**THE WINDOW IS CLOSED.** For a few hours on 2026-09-11 (`beda6b77`, site-dock 18) a coupled run
— `--couple-channels`, one work unit driving a store leaf and a fulfillment leaf through one
batch loop, stamped as `run_layout.json`'s `coupled` — was deliberately byte-identical to the two
uncoupled runs it replaced, and a test pinned it that way. **Site-dock 19 (`9b0e21e8`) ended
that**, and the pinning test was amended rather than deleted: it now asserts the relationship
(one crew, one uid block, the same people in both channels' DBs) and reports the size of the move.

**What changed.** The site's putters are now fielded ONCE across both leaves (one shared
`list[float]` of worker clocks) instead of once per leaf. Before 19 the two leaves each fielded
the whole derived site crew, so the site's put labour was double counted. So on a coupled run:

- absolute put and travel numbers are **not comparable** across that commit — the same site now
  has half the putters it used to appear to have;
- put rows are stamped from a SITE day start rather than either leaf's batch start, so their
  absolute instants move even where the work does not;
- put uids move: the block now starts above BOTH channels' picker counts, so a putter is the same
  person in both DBs (measured on a 12-batch pair: fulfillment's putters went from `[20, 21]` to
  `[25, 26]`);
- put SECONDS move a little too (measured ±0.7–4.6% per leaf), because the day is now divided
  between the channels and re-drained, which changes which unit reaches which bin.

**Uncoupled and flag-off are unaffected and were proven so** — row-for-row zero against a HEAD
copy, plus both preflight canaries.

**Why:** the old note told a reader that a coupled-vs-uncoupled tie is the feature working. That
is now exactly backwards: a tie means the pool did not engage, which is worth investigating.

**How to apply:** date the run. A coupled run from before 19 carries the double count and its put
numbers read as if the site had two crews; one after it does not. Never compare absolute put or
travel across that boundary, and never quote a pre-19 coupled put utilization. Note also that
coupling now REFUSES a run with no derived staffing block or without the working-day grid, so a
coupled run necessarily has both. Related: [[a-grant-is-not-an-output]],
[[per-item-charge-hard-break]], [[derived-fill-is-the-fourth-comparability-break]],
[[lead-aware-record-is-the-fifth-comparability-break]], [[site-dock-is-shared-across-channels]].
