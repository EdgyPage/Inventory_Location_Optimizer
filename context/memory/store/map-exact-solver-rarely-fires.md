---
name: map-exact-solver-rarely-fires
description: "The Map family's exact-LAP gate admits nearly every BinKey class by count but almost none by units; greedy earns most of the Map-family result at production scale"
metadata:
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T19:15:00.000Z
---

Measured on the Experiment-8 catalogue (263,257 SKUs, 398,500 bins):
`_optimal_work_assign`'s exact-LAP gate (`Warehouse/inventory/inventory_optimal.py`,
`_LAP_CAP = 1200`, admits when `n <= _LAP_CAP` AND `n * m_cnt <= 4_000_000`) took the exact
branch on **320 of 1,632 BinKey classes but only 0.09% of assigned stocking units**. The classes
small enough to solve exactly are the nearly-empty ones — class-weighted the split reads like a
fifth of the work, unit-weighted it is a rounding error. The greedy fallback is what actually
earned every published Map-family result at this scale.

**Why this matters:** `docs/experiments/experiment-8/full-results.md` originally described Map as
running "the full linear assignment problem"; that was wrong at scale and has been corrected
(section around line 117-118, "Measured: 320 of 1,632 bin classes take the exact ... but only
0.09% of the stocking positions being assigned").

**How to apply:** never report a solver split by class count alone — always weight by units
assigned, since the two numbers tell opposite stories. If a future gate change (raising
`_LAP_CAP`, or a faster LAP solver) is proposed to "let Map solve more exactly," check the
unit-weighted split first: at this catalogue size the ceiling on possible improvement is under 1%
of assigned units no matter how high the gate goes, because large classes dominate units and stay
outside any practical exact-solver budget.

Related: [[run-dossier-map-precompute]].
