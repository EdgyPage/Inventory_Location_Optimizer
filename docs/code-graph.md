# Code map & call-graph explorer

An interactive, **offline-capable** map of every module, class, function, and constant in the
simulator — signatures, docstrings, callers/callees, layer boundaries, and inefficiency
signals. Generated from the verified call graph (`context/arch/graph.json`) and kept in sync
with the code by the architecture-maintainer, so it never drifts.

[Open the code map ▸](architecture/index.html){ .md-button .md-button--primary }
[Ego-graph explorer ▸](architecture/explorer.html){ .md-button }

## What's inside

- **A page per symbol** — one description page for every function, class, module, and
  constant (~1,400), with its signature, docstring, and linked callers/callees. The exact count
  is whatever `context/arch/nodes.json` holds at the last rebuild.
- **An ego-graph explorer** — click any node to expand its call neighbourhood; the graph and
  the pages mirror the same data.
- **Layer & boundary view** — modules grouped by architectural layer, with cross-layer
  coupling and the import invariants the domain engine upholds.
- **Inefficiency signals** — import cycles, high fan-in/out functions, and orphan public
  functions worth reviewing.

Entry points to start from: `run_simulation.main` (the simulation + what-if harness),
`run_analysis.main` (the plotting pipeline), and `_run_strategy_worker` (the per-strategy
worker body). The suite is self-contained static HTML — it also opens directly from
`docs/architecture/index.html` with no server.
