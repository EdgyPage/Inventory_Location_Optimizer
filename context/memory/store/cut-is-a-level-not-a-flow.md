---
name: cut-is-a-level-not-a-flow
description: "put_queue/dock `cut` resets every batch but its VALUE is a level; summing it inflated a shipped report 101x"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T02:37:33.691Z
---

`PutQueue.cut`, `Dock.cut`, `batch_stats.recv_cut` and `put_queue_state.cut` all reset every
batch, so they look like flows and were labelled FLOW in four places. They are **levels**. What
each records is `len(items)` at the whistle — the same standing queue, re-counted from scratch
each batch. A unit that waits ten batches is counted ten times.

Measured on a 200-batch stress run (2026-08-25): `recv_cut == recv_depth` in **200 of 200**
batches, zero exceptions. `Diagnostics/receiving_report` published `SUM(recv_cut) = 619,418`
against a dock that never exceeded 6,162 — **101x**, as its headline number.

**Why:** being reset per batch is what makes a counter *per-batch*; it is not what makes it
*summable*. The three counters beside it (`admitted`, `placed`, `blocked`) really are flows, and
`cut` was labelled one by analogy.

**How to apply:** never `SUM(cut)` or `SUM(recv_cut)`. The additive statistic is the **count of
batches where it is non-zero** — how often the boundary bit. `MAX(depth)` is the other honest
number. Keep it beside `depth` regardless: `depth > 0, cut = 0` is a backlog nobody was stopped
from clearing, and depth alone cannot say that. Related: [[putaway-seams-for-inbound]],
[[one-clock-one-speed-one-config]].
