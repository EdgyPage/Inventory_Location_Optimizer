# context/ — machine-parsable flow + artifact specs

last-synced-commit: 343c98f1d04373bc4c323e74ae2266714103f6e5  <!-- updated by the context-maintainer agent after each sync -->
arch-synced-commit: 876f429edb04fc4ca14b2faac70e35e91d7582fc  <!-- updated by the architecture-maintainer agent after each sync (context/arch/ + architecture.yml + files.yml + docs/architecture/) -->

Verifiable documentation of the pipeline's code flow, designed for BOTH humans and
downstream design programs (the MkDocs results site, Claude Design). Every
`name @ file` anchor, artifact filename, and DB table named here is asserted to
exist in the code by `python context/verify_context.py` — which also runs in the
ordinary suite as `Tests/test_context_sync.py`, so drift fails CI-style rather
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

## Architecture layer (context/arch/ + architecture.yml + files.yml)

A second verified layer captures the FUNCTION-CALL structure and a per-file catalog, for
humans, agents, and a renderer alike. `context/arch/extract.py` walks the source with the
stdlib `ast` and emits `arch/graph.json` (nodes = modules/classes/functions/consts as
`{name,file,kind}` anchors; edges `kind ∈ {calls,imports,ref,dispatch}`). `architecture.yml`
asserts CLAIMS against it and `context/arch/verify_architecture.py` (gated by
`Tests/test_architecture_sync.py` + `Tests/test_files_catalog_sync.py`) enforces:
**SCOPE** (every `backbone` edge exists in the graph), **BOUNDARY** (no import crosses a
`forbid` layer pair), **anchors** (reusing `verify_context.check_symbol`), **up-to-date**
(graph == a fresh extract), and **CATALOG** (every in-scope `.py` is in `files.yml` exactly
once). The empirical `Tests/test_architecture_coverage.py` asserts declared `hotpaths`
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
