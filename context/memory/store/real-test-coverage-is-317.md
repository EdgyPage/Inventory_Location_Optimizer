---
name: real-test-coverage-is-317
description: The unit+integration suite reported 374 passing but 57 of those tests could not fail — real enforcing coverage was 317
metadata: 
  node_type: memory
  type: project
  originSessionId: f8316019-3a49-404c-ad0d-e5f670ed885b
  modified: 2026-08-14T13:04:04.427Z
---

As of 2026-08-13, `python -m pytest Tests/unit Tests/integration -q` reported **374 passed**, but
**57 of those tests contained no raising assertion**. Real enforcing coverage was **317**.

The 57 lived in six legacy `check()`-harness files under `Tests/unit/`
(`test_warehouse_sizing`, `test_equilibrium_reorder`, `test_placement_lifecycle`, `test_palletizing`,
`test_assignment_functions`, `test_reorder_queue`) holding **217 `check()` calls and 6 real
asserts**. `check(label, cond)` prints PASS/FAIL and increments a counter; every `fail()` body is a
`print`. `test_reorder_queue.py` had **zero** `def test_` functions — it collected nothing at all.
Four tests additionally hard-returned after a failed check, exiting early and reporting pass.

As of 2026-08-14 all six files have been converted to real `assert`-based tests (each file's
docstring now says so explicitly, e.g. `Tests/unit/test_reorder_queue.py:37-39`). `grep -c "check("
Tests/unit/*.py` still returns non-zero on a few files, but only inside these historical docstrings,
not as live calls. `Tests/unit Tests/integration` now collects **402** tests, up from 374/317.

**Why:** a green suite was the evidence used to justify "nothing broke" across a large refactor.
That evidence was ~15% fiction, and the gap is invisible — the files look like tests, collect like
tests, and report like tests.

**How to apply:** when a suite's pass count is offered as proof, check that the assertions can
actually raise. The cheap probe is `grep -n 'check(' Tests/**/*.py` — every surviving hit should be
inside a docstring explaining the history, never a live call. If a conversion of vacuous tests
leaves the pass count unchanged, the conversion was cosmetic. See [[claude-md-section-3-traps]] —
CLAUDE.md §3 carries this warning for the same reason.
