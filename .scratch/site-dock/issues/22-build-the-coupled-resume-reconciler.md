# Build the coupled resume reconciler

Type: task
Status: open

AFK. **Takeable now** — graduated from the map's "remaining builds" fog by the execution
override (map Notes). [Design the coupled unit's resume guard](10-design-the-coupled-resume-guard.md)
settled the rule; its two byte-identical precursors landed as
[Close the torn-finalize window](16-close-the-torn-finalize-window.md), and
[Build the coupled work unit](18-build-the-coupled-work-unit.md) made the pair it reconciles
exist. Nothing else on this map blocks it and it blocks nothing, so it runs alongside
[Build the site space view](20-build-the-site-space-view.md) and
[Re-shape the funnel spec for arm pairs](23-reshape-the-funnel-spec.md).

## Question

Build 10's answer, the coupled half: `_reconcile_coupled_unit`, the two-leaf completeness test,
the torn-pair repair with its `sim_meta.json` removal and leaf reset, the site-DB arm of that
reset, and the `coupled` refusal reason for batch-grain resume.

Two facts from later tickets that the design predates:

- **`run_layout.json` carries `coupled` and resume restores it** (18). A run that resumed without
  it would rebuild per-channel units over a tree whose leaves were written by coupled ones, so
  the marker is the reconciler's ground truth for "was this pair coupled", not an inference from
  the uid.
- **A unit returns one result PER LEAF** (18), and `group_keys` and `leaves` are positional with
  a length check. A coupled unit's failure already blocks BOTH leaves from finalizing, which is
  the completeness property this reconciler is repairing around rather than establishing.

## What proves it

- **The planted four-state matrix**, with its mutation sabotages and **one fault-injected torn
  tree** — a hard mid-flight kill, not a hand-built directory. Memory
  `resume-architecture-verified-sound` is emphatic that the "strategy-level reset" log line is
  NOT evidence of a working resume; only a real kill-and-resume is.
- **Flag-off and uncoupled unchanged, MEASURED**: both preflight canaries, plus a row-level diff
  against a `git archive HEAD` copy on a resumed uncoupled run.
- Nine verifiers; `Tests/architecture` baselined by diffing the failure LIST against a
  `git archive` copy, never the totals (memory `arch-tier-is-red-on-head`).
