---
name: the-suite-restores-config-after-every-test
description: "Tests/conftest.py has an autouse fixture restoring CONFIG after every test; without it one e2e test poisons unit tests twenty minutes later and every tier still passes alone"
metadata:
  node_type: memory
  type: project
---

`sim_config.CONFIG` is a module-level dict mutated IN PLACE and shared by the whole pytest
session — deliberately, so accessors read live values and a CLI override is visible everywhere.
The cost is that production code called from a test writes CONFIG for every later test, in every
later FILE and TIER.

`run_analysis._apply_run_shape` writes `sampler` (falling back to `'v1'`), `work_day_seconds` and
`releases_per_day` (to `None` on a pre-field spec) **unconditionally**, and nothing put CONFIG
back. Symptoms seen 2026-09-16: a unit test asserting `sampler == 'v3'` failed, and six more
ERRORED because `_build_parser()` formats one of those globals with `:g`
(`TypeError: unsupported format string passed to NoneType.__format__`, run_simulation.py:732).

**Since 2026-09-16 `Tests/conftest.py` carries an autouse fixture that snapshots and restores
`CONFIG['global']` and `CONFIG['channels']` after every test.** `Tests/unit/test_conftest_restores_config.py`
pins it; disabling the fixture fails exactly three of its five.

**Why it matters even though it is fixed:** the symptom is invisible per-tier. Every tier passed
on its own; only the full suite failed, and separating the attributable failures from the
pre-existing ones needed a `git archive` control tree — two 25-minute runs.

**How to apply:** if the full suite fails but the tiers pass individually, check that fixture is
still present and still autouse BEFORE investigating anything else. And when the architecture
tier is red, diff against a `git archive HEAD` copy rather than trusting a total — see
[[arch-tier-is-red-on-head]] and [[head-copy-via-git-archive]].
