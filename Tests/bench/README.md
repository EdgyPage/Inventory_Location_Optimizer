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
| `run_digest.py` | content digests over a run's domain tables — the byte-identical gate for results-preserving refactors (`--self-test` first; two run roots to compare) |
| `bench_ranked_assign.py` | `_ranked_assign_impl` (lift waves) |
| `bench_plan_warehouse.py` | warehouse planning: sizing + fielding |

```bash
python Tests/bench/perf_simulation.py
python -m coverage run Tests/bench/coverage_e2e.py && python -m coverage report
```

**Having no importers is normal here** — do not read it as dead code. `perf_simulation`,
`coverage_e2e` and `bench_sections` do have importers; the other three are pure tools.

`smoketest.py` is the full-pipeline one: it simulates, checks the analysis did not half-fail
silently, verifies every artifact of the finished run against the run-tree contract, stages the run
into a throwaway docs experiment, and builds the site strictly. `--profile smoke` is the fast
default; `--profile full` is production scale. `--stages preconditions` is a ~2 s dry gate, and
`--reuse-run DIR` re-verifies a run you already have.

**Caveat:** `bench_sections.py` needs a real comparison `run.log` to parse. It looks under
`COMPARISON_OUTPUT_DIR`, then `PROFILE_INPUT_DIR` (both from `.env`), then the cwd, and skips
cleanly when none holds one. It used to hardcode two result-drive letters; those were machine-local
paths in a tracked file — see CLAUDE.md §5 and `context/guards/path_guard.py`.
