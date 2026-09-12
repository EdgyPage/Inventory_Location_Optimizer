# Build the site space view

Type: task
Status: open

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
[Design the site space view](08-design-the-site-space-view.md) settled the rule and
[Build the coupled work unit](18-build-the-coupled-work-unit.md) made a second leaf exist;
[Build the site put-away pool](19-build-the-site-putaway-pool.md) has since shown what a
per-leaf artefact costs when two leaves share one resource, and this is the same defect one
layer up.

**Why it is its own ticket rather than part of the coordinator's.** `_receive_standing` freezes
`ctx.space` from a `SpaceTimeline` documented as *"one per arm"* and attached to ONE manager, so
under one yard a mixed trailer is ranked on half the site's free space. `freeze(mgr, epoch)`
takes one manager and is pinned PURE by `Tests/unit/test_space_timeline.py`, so this is a real
design cost and not a signature tweak — 01 said so when it graduated 08. The coordinator build
([Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md)) is
blocked on it, because a coordinator that drains one dock while reading one leaf's free space is
a coupled run answering the uncoupled question.

## Question

Build 08's answer: the site-view composer itself, the `empties` regime filter with its
`regime_of(bin)` assertion, the element-wise versions, the per-regime `released_at`, the window
refusal, and the composer's unit test.

## What proves it

- **Flag-off and uncoupled byte-identical, MEASURED** — the preflight canaries plus a row-level
  diff against a `git archive HEAD` copy, the way 19 proved its own (put rows counted BEFORE the
  diff is trusted: an empty table diffs clean).
- The regime filter is proven to FAIL on a bin of the other regime, not merely to be present.
- Nine verifiers; `Tests/architecture` baselined by diffing the failure LIST against a
  `git archive` copy, never the totals (memory `arch-tier-is-red-on-head`).
