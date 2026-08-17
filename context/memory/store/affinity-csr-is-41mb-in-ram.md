---
name: affinity-csr-is-41mb-in-ram
description: "The affinity matrix is ~41 MB per worker in RAM, not the ~291 MB its SQLite file suggests — worker count is not RAM-bound"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5eb3d052-8ad3-4cf3-b0d4-c2ec1fa4ece2
  modified: 2026-08-16T06:33:07.727Z
---

The affinity DB is ~291 MB **on disk**, but the in-process CSR is **~41 MB** (measured:
`Affinity CSR ready : 5,239,290 entries  41 MB` at 150k SKUs). The run log prints this figure
every time, so it is checkable rather than assumed.

**Why it matters:** the on-disk size invites a wrong conclusion — "18 workers x 291 MB won't fit"
— which argues for fewer workers or for worker recycling. Neither is warranted. At 41 MB, 18
workers cost ~740 MB of affinity, a non-issue on this 128 GB box. I made exactly this error once
and had to retract it.

**How to apply:** do not use RAM as an argument for lowering `--workers` or for batching tasks per
worker (see [[worker-recycling-pinned-at-one]] — that path deadlocks and is pinned off). If a run
is memory-pressured, measure it; the affinity matrix is not the cause.
