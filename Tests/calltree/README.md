# Tests/calltree — runtime measurement: timings mapped to function call trees

The framework that answers "which call paths cost, how many times do they run, and how do
they scale" — the evidence layer performance refactors must cite before touching the hot
path. THREE files here are collected by pytest (`test_*`), and they are not all cheap:
`test_calltree_smoke.py` and `test_calltree_anchors.py` run in seconds, but
`test_rank_cache_equivalence.py` takes **12-13 minutes** — it simulates at a scale where
reorder-time placement actually fires, which is the entire reason this package exists.
Everything else is a hand-run tool for the occasional rigorous session.

Nothing here is in a CI gate. That is a standing risk, not an oversight to route around:
three dead frozen-oracle tests and a never-executed feature were both found rotting in this
directory in 2026-08. Run `python -m pytest Tests/calltree -q` and read the result before
trusting any number produced from this package.

| File | What it is |
|---|---|
| `calltree_tracer.py` | deterministic call-tree tracer (`sys.setprofile` + `threading.setprofile`): path-keyed aggregation, exact call counts, worker-thread capture, `t_*` section vocabulary, SECTION_MAP, counts fingerprint |
| `calltree_scenarios.py` | seeded scenario tiers with REAL reorder-time placement (see the trap below); meso mirrors `strategy_runner`'s batch loop on the production `DeferredPickSimulation` engine |
| `calltree_capture.py` | CLI: two-pass capture (untraced walls = truth; traced tree = attribution) → `out/*.json`; `--cprofile` tier-B cross-check; `--speedscope` export |
| `calltree_render.py` | CLI: captures → `out/render/viewer.html` (offline, file://-safe icicle + table + A/B) + PNG section bars |
| `calltree_compare.py` | CLI: diff two captures — exact count deltas (a count drift at matched sizes is a code-shape change), tolerance-banded section walls; nonzero exit on drift |
| `calltree_growth.py` | CLI: size-ladder runner + log-log exponent fitting — the O(n²)-hidden-at-small-scale detector. `--ladder meso` = minutes; `--ladder deep --workers 18` = the ~1-hour real-run session |
| `test_calltree_smoke.py` | collected: the framework measures what it claims (threads seen, placement fires, sections cover the wall, counts deterministic) |
| `test_calltree_anchors.py` | collected drift gate: SECTION_MAP symbols resolve, section vocabulary matches `strategy_runner`'s log line, engine identity holds |
| `test_rank_cache_equivalence.py` | collected, **~12-13 min**: the rank-cache fast path is byte-identical to the exhaustive one at a scale where reorder placement fires. Do NOT shrink it to make it fast — at `n_batches=2` the arm produces 0 reorders and the test silently measures nothing. Fast unit-scale counterparts already exist (`Tests/unit/test_*_pool_equivalence.py`, 125 tests in ~4 s) |
| `calltree_memory.py` | CLI: the memory dimension — meso tracemalloc (per-section allocation peaks, retained growth, GC pauses, live-object census, top allocation sites) + deep worker-tree RSS sampling via psutil; `--ladder skus` fits k_mem exponents |
| `calltree_store.py` | archival naming + `out/index.json` registry — results are EVIDENCE and are never overwritten; every artifact lands in `out/archive/` stamped with UTC time + repo commit, and the index carries a summary (exponents, offenders, peaks) queryable across sessions. Run it directly to migrate legacy flat-named files |
| `static/` | tracked viewer template (copied beside generated `data.js` — data as a sibling script, never fetch, so `file://` works) |
| `out/` | gitignored captures/renders — regenerate any time. `out/archive/` + `out/index.json` are the permanent record within this machine; cite artifacts by their stamped filename |

## The one-hour rigorous session

This library is for occasional deep investigation, not per-change CI:

```bash
python Tests/calltree/calltree_capture.py --tier meso --seed 42     # tree + counts, ~1 min
python Tests/calltree/calltree_growth.py  --ladder meso --knob skus # exponents, ~1 min
python Tests/calltree/calltree_growth.py  --ladder meso --knob batches
python Tests/calltree/calltree_growth.py  --ladder deep --workers 18  # real runs, ~1 hour
python Tests/calltree/calltree_render.py                            # open out/render/viewer.html
```

Growth fitting reads call counts against the ladder knob: counts are exact and
deterministic under fixed seeds, so a function whose count exponent is well above 1
is a super-linear suspect long before its wall share is visible at test scale. First
meso run already flagged `_travel_balanced_impl.<locals>._aisle_best` at k≈1.66
(11.7k → 1.14M calls across a 16× SKU ladder).

## Two caveats that keep results honest

- **Two-pass overhead.** Deterministic tracing multiplies wall time ~40× at small scale.
  Every capture therefore stores UNTRACED section walls (the regression truth) beside the
  traced tree (shape/counts/attribution). Never quote traced seconds as performance.
- **The dead-placement trap.** `Tests/bench/perf_simulation.py::_build_inventory` sets no
  `reorder_point`/`equilibrium_qty`, so reorder-time placement — the entire `t_reord`
  section and every assignment function — never executes in tools built directly on it.
  `Tests/bench/profile_lifecycle.py` inherits that AND runs the non-production
  `PickSimulation` engine: read its historical numbers with both caveats. Scenarios here
  set real reorder fields (`calltree_scenarios.set_reorder_fields`) and the smoke test
  asserts `placements > 0` so the trap cannot silently return.

## Drift protection

`test_calltree_anchors.py` pins the framework's anchors into product code (SECTION_MAP
symbols, the `t_*`/checkpoint-log vocabulary, engine identity, reorders firing). When a
hot-path refactor renames or moves something, that test names the exact map entry to
update. The `.claude/agents/` code-reviewer and test-developer notes carry the same
reminder for humans and agents.
