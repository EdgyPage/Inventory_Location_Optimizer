# bench — hand-run benchmarks and shared fixtures

Nothing here is collected by pytest (no `test_` prefix). These are tools you run deliberately, plus
the scenario builders the real tests import.

`Tests/conftest.py` puts this directory on `sys.path`, so tests import the helpers by bare name
(`from perf_simulation import _build_inventory`).

| Module | Run it for |
|---|---|
| `perf_simulation.py` | end-to-end simulation performance — **also the shared fixture source** |
| `coverage_e2e.py` | single-process full-pipeline exerciser; drives `test_architecture_coverage` |
| `profile_lifecycle.py` | fine-grained lifecycle profiling of the batch simulation |
| `bench_sections.py` | Amdahl baseline — where wall-time actually goes per batch |
| `bench_ranked_assign.py` | `_ranked_assign_impl` (lift waves) |
| `bench_sample_to_capacity.py` | warehouse planning / capacity sampling |

```bash
python Tests/bench/perf_simulation.py
python -m coverage run Tests/bench/coverage_e2e.py && python -m coverage report
```

**Having no importers is normal here** — do not read it as dead code. `perf_simulation`,
`coverage_e2e` and `bench_sections` do have importers; the other three are pure tools.

**Caveat:** `bench_sections.py` hardcodes `F:`/`H:` result-drive paths.
