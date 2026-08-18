# Tests — grouped by what a failure MEANS

| Directory | A failure here means |
|---|---|
| `unit/` | domain behaviour changed — placement, sizing, reorder, palletizing, regimes |
| `integration/` | the run harness changed — schema, manifest, layout, recovery, what-if, analysis |
| `e2e/` | the wiring changed — multi-process runs through the real `run_simulation` path |
| `architecture/` | the *documentation* drifted from the code — graph, catalog, context, registries |
| `bench/` | not collected; hand-run tools + shared fixtures (see its README) |
| `calltree/` | the runtime-measurement framework itself broke — tracer, scenarios, section anchors, or determinism (its CLIs are hand-run; see its README) |
| `gpu/` | dormant GPU island; skipped by the routine suite (`-k "not gpu"`) |

```bash
python -m pytest Tests/ -q -k "not gpu"      # the routine suite
python -m pytest Tests/unit -q               # just the domain
python -m pytest Tests/architecture -q       # just the drift gates
```

## Three constraints on where a test may live

**`Tests/` is deliberately NOT a package.** No `__init__.py`, anywhere. pytest's prepend import mode
puts each test file's own directory on `sys.path`, and that is precisely what makes sibling-helper
imports work. Adding `__init__.py` breaks them.

**Helper-sharing tests must be co-located.** `unit/test_channel_strategy_subset.py` does
`from test_fulfillment_channels import _mixed_inventory` — bare name, same directory only.

**Basenames must stay unique across subdirectories,** or pytest raises "import file mismatch".

Tests that climb `__file__` to reach the repo root are depth-sensitive: a test one level deeper
needs one more `dirname`. The `architecture/` group uses `_ROOT` for real paths, so that fails
loudly — but it still has to be right.
