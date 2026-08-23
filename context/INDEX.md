# context/ — machine-parsable flow + artifact specs

last-synced-commit: 1a7a668b15fa2b4347313ac5504216239eda07ee  <!-- updated by the context-maintainer agent after each sync -->
arch-synced-commit: ae74eb1aaf7dfd84e555221915806d052d065a87  <!-- updated by the architecture-maintainer agent after each sync (context/arch/ + architecture.yml + files.yml + docs/architecture/) -->

Verifiable documentation of the pipeline's code flow, designed for BOTH humans and
downstream design programs (the MkDocs results site, Claude Design). Every
`name @ file` anchor, artifact filename, and DB table named here is asserted to
exist in the code by `python context/verify_context.py` — which also runs in the
ordinary suite as `Tests/architecture/test_context_sync.py`, so drift fails CI-style rather
than rotting silently. The `context-maintainer` agent (.claude/agents/) re-syncs
these files incrementally after commits.

| File | What it maps |
|---|---|
| `flows/simulation.yml` | CONFIG → pair discovery → shared assets → per-channel runs → spawn workers → sim DBs → sim_meta |
| `flows/analysis.yml` | channel-run discovery → @evaluation registry (PNGs + series.json) → _aggregate → channel rollup |
| `artifacts.yml` | every artifact: path pattern, writer, readers, schema (sqlite tables / json fields) |
| `architecture.yml` | curated INTENT: layers, import `boundaries`, `backbone` caller→callee edges, `hotpaths` |
| `arch/graph.json` + `arch/nodes.json` | DERIVED truth: the call/import graph + per-node signatures/docstrings, extracted from source (regenerable) |
| `files.yml` | the file catalog: every source + test file's purpose, layer, key symbols, notes |
| `docs/architecture/**` | the generated static HTML code-map suite (per-node/-file pages, ego-graph explorer, layer/catalog/inefficiency hubs); `arch/site_manifest.json` pins it |
| `guards/` | content guards — `path_guard.py` keeps machine-local filesystem paths out of tracked files |
| `memory/` | the durable memory layer — `store/` mirrors the session memory store into git |

Related, and verified the same way but living outside `context/`:
`Optimization/schemas/run_tree/` — the CONTENT-ADDRESSED contracts for a run's on-disk directory
tree (see below). `artifacts.yml`'s `path_pattern`s are kept in step with the head contract.

## Run-tree contract layer (Optimization/runschema/)

`artifacts.yml` documents artifacts for humans; `Optimization/runschema/` makes the tree
RESOLVABLE by code.

**There is no version number.** A schema is identified by the sha256 of its own declared shape:

| File | Role |
|---|---|
| `runschema/schema.py` | THE DECLARATION — `FEATURES`, `LEVELS`, `ARTIFACTS`. Edit this to change the tree. |
| `runschema/contract.py` | Derives `schema_id`, stores documents, maintains `INDEX.json`, verifies the store |
| `runschema/resolver.py` | ONE generic `RunTree` interpreting ANY contract document — no per-schema Python |
| `Optimization/schemas/run_tree/<short>.json` | An immutable document; its filename is its own content hash |
| `Optimization/schemas/run_tree/INDEX.json` | GENERATED: `head`, the mutable `source_fingerprint`, and the parent chain |

The id is derived, never chosen: editing the tables mints a new one automatically, two branches get
different ids instead of both calling themselves "v2", and anyone can re-derive it to check. Prose
(`note`/`condition`/`writer`) is excluded from the hash, so documenting the tree better never mints
a schema. A run stamps `schema_id` into its `run_layout.json` and `runschema.resolver_for(run_root)`
binds to THAT document — so an OLD run stays analyzable after the tree shape moves on. Because
hashes have no order, "current" is a named pointer (`INDEX.json`'s `head`) and history is the
`parent` chain. Compatibility is negotiated by FEATURE, not by comparing numbers.

Two levels are CONDITIONAL and must be handled both ways: `<channel>/` exists only on a mixed
catalog, and `_frozen/<pair>/` only on a multi-cell run. Assuming otherwise is what silently
dropped every store-only run from the what-if scanners.

`runschema/preflight.py` keeps the contract honest and runs automatically at the front of
`run_simulation.py` (skip with `--no-preflight`). It compares source fingerprints first — free
unless something shape-defining changed — then proves the shape with two tiny canary runs (mixed
2-cell + store-only 1-cell, so both forms of each optional level appear). When the declaration
already covers the observed tree it ADOPTS the resulting id and rewrites the downstream orchestrator
files (`artifacts.yml` patterns, `Visualization/static/schema.json`); when the code moved but
`schema.py` didn't, it prints the exact `ARTIFACTS` entries to add (`--apply` inserts them).
`runschema/hook_check.py` is the advisory Stop-hook nag. Gated by
`Tests/integration/test_runschema_contract.py`.

## Architecture layer (context/arch/ + architecture.yml + files.yml)

A second verified layer captures the FUNCTION-CALL structure and a per-file catalog, for
humans, agents, and a renderer alike. `context/arch/extract.py` walks the source with the
stdlib `ast` and emits `arch/graph.json` (nodes = modules/classes/functions/consts as
`{name,file,kind}` anchors; edges `kind ∈ {calls,imports,ref,dispatch}`). `architecture.yml`
asserts CLAIMS against it and `context/arch/verify_architecture.py` (gated by
`Tests/architecture/test_architecture_sync.py` + `Tests/architecture/test_files_catalog_sync.py`) enforces:
**SCOPE** (every `backbone` edge exists in the graph), **BOUNDARY** (no import crosses a
`forbid` layer pair), **anchors** (reusing `verify_context.check_symbol`), **up-to-date**
(graph == a fresh extract), and **CATALOG** (every in-scope `.py` is in `files.yml` exactly
once). The empirical `Tests/architecture/test_architecture_coverage.py` asserts declared `hotpaths`
actually execute under `Tests/bench/coverage_e2e.py::main`. Four dynamic-dispatch layers are
resolved without false edges: mixin methods (MRO pass), string registries + the ProcessPool
spawn (`ref` edges), and the placement closures (the one curated `arch/resolver_hints.yml`).
Regenerate IN ORDER: `extract.py --write` → `extract.py --catalog-merge` →
`extract.py --write-nodes` → `render_html.py --build` (the catalog feeds the site, so it is
built last). `context/arch/verify_site.py` gates the generated HTML (currency + integrity +
dead-links); `--fast` (integrity only) runs in the Stop hook. The `architecture-maintainer`
agent runs the chain, both verifiers, and bumps `arch-synced-commit`. `--catalog-merge`
preserves human-owned `purpose`/`notes` by construction. The suite is published on the
MkDocs site at `/architecture/` (wrapper page `docs/code-graph.md`).

## Guard + memory layers (context/guards/ + context/memory/)

Two layers that verify file CONTENT rather than code structure. Both live under `context/`, which
is outside `CATALOG_ROOTS` (`arch/extract.py`), so files here need no `files.yml` entry.

`guards/path_guard.py` forbids machine-local filesystem paths — drive-letter and home-directory
absolutes, UNC shares, the current username (derived at runtime, never written down), and
session-scoped scratchpad paths — in any tracked file. Repo-relative paths stay legal; the whole
verified anchor layer is built from them. It is enforced at write time: `guards/hook_check.py
--pre-write` is the ONE hook in this repo that fails a call (exit 2), and it **fails open** on any
internal error so a broken guard can never brick a session. A `Stop` scan backstops writes that
bypass `PreToolUse`, such as a Bash heredoc.

`memory/store/` is a git-tracked mirror of the session memory store, which otherwise lives outside
the repo in a directory named after the repo's absolute path — and is therefore orphaned whenever
the repo moves. Direction is live → mirror, always; `sync.py --push` refuses to mirror anything the
guard flags, and refuses entirely when the live store is empty but the mirror is not (that is what a
move looks like — the answer is `--restore`). `verify_memory.py` checks located/parity/index/shape/
links/paths/anchors; `--repo-only` drops the two machine-local checks so a test can run in any
clone. The anchor check is the one that rots unattended: a refactor that moves files silently
invalidates every memory citing them, and the 2026-07 restructure staled 8 anchors across 4 of 8
memories before this existed. The `memory-maintainer` agent owns repairs. Gated by
`Tests/architecture/test_path_guard.py` and `test_memory_sync.py`.

## Schema (v1)

- **flows/*.yml** — `version, flow, summary, entry{command,file,function},
  steps[{id, summary, functions[{name,file,kind: function|class|const}],
  reads[artifact ids], writes[artifact ids], guards[test files], next[step ids]}]`.
- **artifacts.yml** — `artifacts{<id>: {path_pattern, format, writer{function,file},
  readers[], match (literal asserted in the writer file; defaults to the path
  basename), tables[] + schema_file (sqlite), fields[] (json/csv, documentation)}}`.
- **architecture.yml** — `version, layers[{name, match|members, except}],
  boundaries[{forbid: [layerA, layerB|"*"], why}],
  backbone[{src{name,file,kind}, dst{name,file,kind}, via: calls|ref|dispatch, coverage?}],
  hotpaths[{name,file,kind}]`.
- **files.yml** — `version, files{<relpath>: {purpose, layer, key_symbols[{name,file,kind}],
  flows?[flow ids], notes}}`. `purpose`/`notes` are human-owned; `layer`/`key_symbols` are
  regenerated from the code.

## Conventions

- Anchors are exact code identifiers — never paraphrased. `kind: const` anchors
  match `NAME =` / `NAME:` at column 0; functions/classes match their `def`/`class`.
- `guards` are the pytest files that lock a step's behavior (the golden fence for
  numeric steps).
- Store-only runs write artifacts at `<config>/`; mixed-catalog runs one level
  deeper at `<config>/<channel>/` — `path_pattern` encodes this as `[/<channel>]`.
  Downstream parsers key on these patterns; treat changes as breaking.

## Extending

New flow = new `flows/<name>.yml` in the same schema (candidates: `generation`,
`publication`). New artifact = one entry in `artifacts.yml`. Run the verifier;
commit only when it exits 0.

Architecture layer: a new documented call is one `backbone` edge in `architecture.yml`
(set `via` to the real edge kind in `graph.json`); a new import invariant is one
`boundaries` entry (only if the graph already satisfies it). A new source/test file is
catalogued automatically by `extract.py --catalog-merge` (fill its `purpose`, then leave
`layer`/`key_symbols` to the tool). Run `verify_architecture.py`; it must exit 0.
