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
| 15 package READMEs | what belongs in a directory — and what does not |
| `context/architecture.yml` | the layer map and the 19 import `boundaries` |

## 1. Commands that actually work

The eleven gates. **Invocation form is not interchangeable** — `context/` verifiers run by path,
`runschema` CLIs run as modules, and the last one is a pytest selection:

```bash
python context/verify_context.py                      # flow/artifact anchors exist
python context/arch/verify_architecture.py            # graph fresh + boundaries + catalog
python context/arch/verify_site.py --fast             # generated HTML integrity
python -m Optimization.runschema.contract  --check    # run-tree schema not stale
python -m Optimization.runschema.preflight --check    # output tree hasn't moved
python -m Schema.profile_tree --check                 # profiles-tree (catalogue) schema not stale
python -m Schema.store_index --check                  # DB-shape store (Schema/shapes/INDEX.json) not stale
python context/memory/verify_memory.py                # memory mirror + anchors still true
python context/guards/path_guard.py --scan            # no machine-local paths in tracked files
python context/guards/docref_guard.py --scan          # "<doc>.md section N" refs still resolve
python -m pytest Tests/calltree/test_calltree_smoke.py \
                Tests/calltree/test_calltree_anchors.py \
                Tests/calltree/test_scan_width.py \
                Tests/calltree/test_unload_key_tau.py \
                Tests/unit/test_deferred_indices.py \
                Tests/unit/test_conftest_restores_nested_config.py \
                Tests/integration/test_supervisor_broken_pool.py \
                Tests/architecture/test_digest_surface.py -q   # the instruments still measure
```

The last gate, the pytest selection, is ~25 s and exists because the instruments it covers are the ones that
fail SILENTLY and in the direction of looking healthy. `Tests/calltree/` was in no gate at
all until 2026-09-16, and three dead frozen oracles plus a never-executed feature were found
rotting in it; `run_digest.py`, the byte-identity tool, was dead twice for the same reason
(`test_digest_surface.py` is what now notices). `test_deferred_indices.py` joined on 2026-09-18, and it is the only check in this repo that
reasons about a FINISHED sim DB rather than about declared DDL. The arm writer creates its
database UNINDEXED and builds the indexes at run end; a regression where that build quietly
created nothing would pass the digest surface (which enumerates tables) and the schema
identity gate (which hashes the declaration) with everything green. `test_scan_width.py`
joined for the older reason: it is a measurement instrument, and instruments that live
outside a gate here have rotted three times. `test_unload_key_tau.py` joined on 2026-09-18
for the same reason: it pins the arithmetic of the instrument that refuted the per-trailer
unload key, so the refutation can be re-taken with its numbers meaning the same thing.

`Schema.store_index --check` joined on 2026-09-18 for a fourth silence. The DB-shape store
had a Stop hook that ALWAYS exits 0 and no blocking form, while its sibling store had
`Schema.profile_tree --check` all along. That day two DDL-defining sources were edited and
committed with every gate green; the only check that noticed sits in `Tests/architecture`,
the tier a routine `Tests/unit Tests/integration` subset never reaches. Editing anything in
`Schema/store_index.py`'s `DDL_SOURCES` -- which includes `Warehouse_Data.py` and
`runtime_metrics.py`, not only `Picking_Data.py` -- is what trips it; the remedy is always
`python scripts/schema_report.py --sync`.

Two more joined on 2026-09-18 for the same shape of silence. `test_conftest_restores_nested_config.py`:
the autouse CONFIG restore copied two levels while `cells._apply_cell` writes three, so a split
applied by one test rode into every test after it with the fixture reporting nothing.
`test_supervisor_broken_pool.py`: a pool whose every worker died at import hung the phase-2
driver for 37 minutes at zero CPU with `worker pool BROKEN` as the log's last line; every
existing supervisor test fakes the pool, and a real pool with SMALL arguments returns fine --
the hang needs one unit argument larger than a Windows pipe buffer (8 KiB), which the real
payload always is. It runs the reproduction in a subprocess under a watchdog, so it fails
rather than hangs.

It deliberately does NOT include
`test_rank_cache_equivalence.py` — that one is 7-13 minutes and is a pre-merge cost, not a
per-change one.

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
- **Schema changes ride the pipeline, never a consumer edit.** `--sync` before a DDL edit,
  `--accept` after (adopts the outgoing shape; you write the commit-window comment). A new DB
  writer calls `Schema.compat.stamp_checked` at creation; a new DB consumer declares a
  `Requires` or uses `Schema.dataset.bind`; SQL belongs in a named query beside the family,
  with a per-vintage `dataset.override` when a shape moves. New run-tree consumers resolve paths
  via `runschema.resolver_for` accessors (`path`/`leaf_path`/`glob`) — never join strings.
  `docs/design/SCHEMA_COMPATIBILITY.md` is the full pattern; `schema-maintainer` owns it.

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
- **A `check()`-harness test passes under pytest while failing** — its `fail()` body is a `print`
  plus a counter, so nothing raises. The legacy files that did this were converted to real
  `assert`s; the pattern is **gone from `Tests/` and must not return**. Same failure mode, same
  silence: a test module with no `def test_` function at all collects nothing and reports success.
- **Without pyyaml, 7 of the 31 `Tests/architecture/*` files `importorskip` and vanish** — and
  they are exactly the sync gates (architecture, HTML site, graph extract, files-catalog,
  context, architecture-coverage, figure-registry) — and one of those seven needs `coverage`
  on top. The other twenty-four still run, so the suite looks healthy while the generated
  docs and the `context/` anchors rot unchecked. (This line read "6 of the 13" until
  2026-09-16, and its first correction still named `column-semantics` — the one file whose
  docstring says it imports nothing optional so that it cannot vanish. A count in prose
  rots; a count in prose that has already been corrected once rots just as fast.)
- **`nbstripout` is a git filter whose command lives in uncommitted `.git/config`.** A fresh clone
  needs `pip install nbstripout && nbstripout --install` or notebook checkout fails.
- **`run_analysis.py` takes a CELL directory; handed a run root it does nothing and exits 0.**
  It logs `Config stage: 0 job(s)` and reports success, because a run root contains no channel
  runs to walk. The whole-run entry point is `python -m Optimization.analyze_run <run_root>`,
  which drives every cell and then the cross-cell what-if writers.

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

## Agent skills

Per-repo configuration for the installed engineering skills. Each pointer file is the single source
of truth for its topic; edit those, not this summary.

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature-slug>/` — **not** GitHub Issues,
despite the GitHub remote, and `gh` is not installed. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name; here a "label" is a `Status:` line in
the issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root — unrelated to the `context/`
directory, which is a different system. See `docs/agents/domain.md`.

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
