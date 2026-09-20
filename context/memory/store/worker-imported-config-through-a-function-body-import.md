---
name: worker-imported-config-through-a-function-body-import
description: "until 2026-09-19 strategy_runner._build_arm imported load_run_inventory from sim_assets INSIDE its body, and sim_assets imports CONFIG at module level, so every spawned worker imported sim_config at run time while the import-time probe stayed green"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-19T23:55:30.084Z
---

Until 2026-09-19 (fixed in `99982d90`) `Optimization/simdriver/strategy_runner.py:_build_arm`
imported `load_run_inventory` from `Optimization.simdriver.sim_assets` INSIDE its function body
(not at module scope), and `sim_assets` imports `sim_config`/CONFIG at module level. Because the
import happened lazily, at call time, in every spawned worker, every worker DID import
`sim_config` at run time -- for months -- while `Tests/unit/test_config_reaches_the_worker.py`'s
import-time probe (which imports the worker MODULE and inspects its top-level imports) stayed
green: it cannot see a function-body import.

The loader now lives in `Warehouse/generation/generate_inventory.py` beside
`load_inventory_from_db`, a module that imports no CONFIG; `sim_assets` re-exports the name for
its own parent-side callers. `build_shared_assets` also stopped defaulting `keyframe_interval` to
`CONFIG['global']['keyframe_interval']` in its function SIGNATURE (an import-time snapshot no CLI
override could reach) -- it now reads CONFIG at call time via `None`.

**Why:** the guard test only ever checked import-TIME (module-level) imports; a lazy
function-body import of a CONFIG-importing module defeated it silently.

**How to apply:** the guard test now includes an AST walk of a worker function's body-level
imports resolved one level, with a sabotage twin proving it fails on the old shape
(`Tests/unit/test_config_reaches_the_worker.py`). A worker-side import of any
`Optimization.simdriver` module other than `strategy_runner`'s own declared dependencies is
suspect until checked against that test; a NEW function-body import inside a worker path needs
the same AST-level scrutiny, not just an import-time check.
