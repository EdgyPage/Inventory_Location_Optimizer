# CLAUDE.md — what to know before touching this repo

A warehouse-placement simulator: generate a catalogue, place inventory by competing assignment
functions, simulate picking, compare labour cost. This file holds only what is **cross-cutting and
non-obvious**. Granular behaviour lives in docstrings, directory membership in package READMEs, and
verified anchors in `context/` — see the last section before adding anything here.

| Read this | When |
|---|---|
| `README.md` | the human map: setup, the 5-step workflow, and every CLI's arguments |
| **`context/INDEX.md`** | **densest file in the repo.** Before touching `context/`, `Optimization/runschema/`, or any anchor |
| `Tests/README.md` | what a failure in each test directory *means*, and the 3 placement constraints |
| 14 package READMEs | what belongs in a directory — and what does not |
| `context/architecture.yml` | the layer map and the 19 import `boundaries` |

## 1. Commands that actually work

The eight gates. **Invocation form is not interchangeable** — `context/` verifiers run by path,
`runschema` CLIs run as modules:

```bash
python context/verify_context.py                      # flow/artifact anchors exist
python context/arch/verify_architecture.py            # graph fresh + boundaries + catalog
python context/arch/verify_site.py --fast             # generated HTML integrity
python -m Optimization.runschema.contract  --check    # run-tree schema not stale
python -m Optimization.runschema.preflight --check    # output tree hasn't moved
python context/memory/verify_memory.py                # memory mirror + anchors still true
python context/guards/path_guard.py --scan            # no machine-local paths in tracked files
python context/guards/docref_guard.py --scan          # "<doc>.md section N" refs still resolve
```

Tests. **There is no pytest config file anywhere** — no `pytest.ini`, no `pyproject.toml`, no
markers, no `addopts`. `-k "not gpu"` is a hand-typed convention, not a default:

```bash
python -m pytest Tests/unit -q            # domain behaviour — fast
python -m pytest Tests/architecture -q    # drift gates — slow, builds the HTML site twice
python -m pytest Tests/ -q -k "not gpu"   # the routine suite — EXCEEDS 40 MINUTES
```

Prefer the narrowest subset that covers the change. The full suite is a session-sized cost.

Regenerating the architecture layer — **the order is load-bearing**, the site is built last because
the catalog feeds it:

```bash
python context/arch/extract.py --write           # -> arch/graph.json
python context/arch/extract.py --catalog-merge   # -> files.yml (new entries land as purpose: TODO)
#   FILL every new `purpose: TODO` in context/files.yml HERE, before the next step
python context/arch/extract.py --write-nodes     # -> arch/nodes.json
python context/arch/render_html.py --build       # -> docs/architecture/**
```

Or hand the whole chain to the `architecture-maintainer` agent.

## 2. Conventions

- **Imports are package-absolute** — `from Warehouse.catalog.Order import Order`. The only legal
  `sys.path.insert` sites are entry-script bootstraps and `Tests/conftest.py`. A bare-name import
  (`from Assignment_Functions import ...`) is a bug: it silently stops resolving when a module moves.
- **Style lives in the code** — heavy module/function docstrings, inline comments, box-drawing
  banners (`# ── … ──`). Match the surrounding file; do not impose an external ruleset.
- **Reuse before reinvention.** `Warehouse/kernel/regime.py:regime_of`;
  `Warehouse/inventory/inventory_common.py` (`_wp_for`, `tier_ranks_for`, `BinKey`);
  `Warehouse/kernel/cost_model.py` (`sec_per_inch`, `height_multiplier`, `handle_var`);
  `Warehouse/layout/Aisle_Dimensions.py` (`uniform_aisle_bins`, `catalog_aisle_bins`).
- **Byte-identical discipline** — a new feature must be a strict no-op when its flag or regime is off
  (`wp.by_regime is None`, `channel_regime is None`, no fulfillment items). Anything that could
  perturb the store-only path needs a test proving equivalence.
- **Regime is single-valued per entity.** A bin/order/aisle is exactly one regime; per-regime values
  (`b._D`, `order.labor_cost`) stay one value each.
- **Determinism** — draw from a seeded `random.Random` / `np.random.default_rng`, never the bare
  global. Reset mutated class state (`Aisle.next_aisle_id`, `Order.next_sku`) where it matters.
- **Floats compare with a tolerance, never `==`.**
- **Spawn, not fork.** `ProcessPoolExecutor` worker entry points and their arguments must be
  module-level and picklable.
- **Never commit `*.db` or `comparison_*/`.** Run output is ~500 GB per sweep (measured; see the
  README's size section). Only curated PNGs and config/params JSON belong in git.
- **Tests use real `assert`.** Never add a legacy `check()`-based test (see §3).

## 3. Silent traps — each of these fails with no error message

- **`Optimization/config/sim_config.py` `_REPO_ROOT` is depth-sensitive.** It is used for `.env`
  loading, not imports. A wrong `..` count silently stops `.env` loading, `COMPARISON_OUTPUT_DIR`
  falls back to the source tree, and a run writes hundreds of GB *into the repo*. Guarded by an
  `assert` — do not remove it.
- **Consume run-tree levels positionally, never by directory name.** The store *config* and the
  store *channel* are both named `store`, so `<cell>/<pair>/store/store/` is a real path. Two levels
  are conditional: `<channel>/` exists only on a mixed catalogue, `_frozen/<pair>/` only on a
  multi-cell run. Assuming otherwise silently dropped every store-only run from the what-if scanners.
  Use `runschema.resolver_for(base_dir)`; never join path strings.
- **7 legacy `check()`-harness test files print PASS/FAIL but never raise** — they pass under pytest
  while failing. `Tests/unit/test_reorder_queue.py` has zero `def test_` functions at all.
- **Every `Tests/architecture/*` file does `pytest.importorskip('yaml')`.** Without pyyaml, all eight
  drift gates *skip* and the suite is green while the docs rot.
- **`nbstripout` is a git filter whose command lives in uncommitted `.git/config`.** A fresh clone
  needs `pip install nbstripout && nbstripout --install` or notebook checkout fails.

## 4. Where things are

`Warehouse/` is the domain engine (`kernel`, `layout`, `catalog`, `inventory`, `placement`,
`picking`); `Optimization/` is the run harness (`config`, `persistence`, `metrics`, `runschema`,
`simdriver`, `simconfig`, `Performance_Evaluations`). CLI entry points stay at each package root.
Full tables in `README.md`.

**Anything citing `Warehouse/Assignment_Functions.py`, `Warehouse/fast_pick.py`,
`Optimization/gpu_*.py`, or a flat `Tests/test_*.py` predates commit `dce445b` and must be
re-resolved** — grep `context/files.yml` or use `git log --follow`.

## 5. Git, paths, and memory

- Day-to-day commits go on **`develop`**. No feature branches or PRs unless asked; `main` is the
  GitHub default but is curated, updated only at milestones via squash-merge. Do not commit to
  `main` directly.
- **Stage only the files the request touches** — never bundle unrelated config or formatting tweaks
  into someone else's commit.
- **Do not push unless asked.** Committing is routine here; publishing is not.
- Maintainer agents (`architecture-`, `context-`, `memory-maintainer`) do not commit at all — they
  leave their changes in the working tree for review.
- **Never write a machine-local path into a tracked file or a memory** — no drive-letter absolutes,
  no home-directory absolutes, no username, no scratchpad path. Name the `.env` key
  (`COMPARISON_OUTPUT_DIR`, `PROFILE_INPUT_DIR`) or use a `~/`-relative form. Enforced by
  `context/guards/path_guard.py`, which blocks the write. (This paragraph deliberately contains no
  example — the guard would flag it, and an allowlist entry for our own docs is worse than prose.)
- Durable cross-session facts live in `context/memory/store/` (a git-tracked mirror of the session
  memory store). The `memory-maintainer` agent owns it; see `context/memory/README.md`.

## 6. What does NOT belong in this file

It loads into every session, so a fact earns a line only if **getting it wrong costs more than ten
minutes and produces no error message**, and it is not discoverable by reading one file. Otherwise:

| The fact is about… | It goes in… |
|---|---|
| one function or knob | that code's docstring |
| what may live in a directory | that package's `README.md` |
| a verifiable `name@file` anchor | `context/` (and its verifier) |
| a decision, a rejected alternative, a machine fact | a memory (`context/memory/store/`) |

Writing it in two places creates two things to rot.
