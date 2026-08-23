---
name: publish-experiment
description: Publish a finished comparison run to the docs site as the new current experiment, deprecate the previous one, and harden the pages through the four-persona stakeholder reviewer loop until the managerial pair reports no blocking gaps. Use when the user asks to upload/publish run results to the site, create the next experiment, or run the stakeholder review loop. Args - the run root (a comparison_whatif_* or comparison_* dir) and optionally the experiment name (defaults to the next number).
---

# Publish an experiment (the Experiment-8 recipe, corrections included)

The full pipeline that took `comparison_whatif_20260820_193432` to the published,
four-persona-reviewed Experiment 8. Every step below was actually executed; the **traps** are
mistakes made and caught en route — do not rediscover them.

## 0. Preconditions

- The run root must carry its analysis artifacts: the 3 whatif JSONs + whatif PNGs at the root,
  per-leaf curated figure PNGs, and per-cell `channel_rollup*.csv`. A run made with the inline
  analysis (default since `2cdea43`) has all of them; regenerate any gap with
  `Optimization/run_analysis.py <cell>`, `run_whatif_{delta,labor,volume} <root>`,
  `run_channel_rollup.py <cell>` (analysis before rollup).  The RUN DOSSIER
  (`<root>/_dossier/`) comes from the run-scope evaluations at the end of `analyze_run`;
  `run_map_precompute <root>` backfills the map family's offline build time and MUST run
  AFTER `analyze_run`, because the dossier stage wipes the tree its per-class census lands
  in.  Pool-driving CLIs run as `-m` modules — a spawn pool launched from a heredoc hangs
  silently. Verify with the resolver
  (`Optimization.runschema.resolver_for`), never by joining path strings.
- Extract the story numbers FROM the artifacts, never from run logs: winners + savings from the
  per-cell `channel_rollup_summary.csv`; day-length/throughput/labor-invariance stats computed
  from `whatif_volume.json` rows; window comparison from `whatif_delta.json`.

## 1. Scaffold + manifest + stage

```bash
python scripts/new_experiment.py --name experiment-N --source <run_root>
```

Then CURATE `docs/experiments/experiment-N/experiment.yml` (gen-manifest overwrites curated
manifests — run it exactly once, then never again for this experiment):

- title (results-first, NO internal jargon — "v2-era" in a title reads as software versioning),
  winners (compute the podium from the rollup CSVs), whatif block (source_run, commit = the
  run's own `run_spec.json:repo_commit`, reference/winner cells, cells/arms/n_batches).
- Keep top-level `schema_id` + `cells` (the strict build verifies the contract doc).
- **TRAP:** copy `configs:` values from the RUN's leaf `config.json`, not from the previous
  experiment's manifest — Exp 8 initially inherited Exp 7's `height_brackets: []` for
  fulfillment while the run's config carried real brackets; a reviewer caught the contradiction.
- Re-run ingest (no `--gen-manifest`) after curating; zero `MISSING` lines required. The
  rollup CSVs stage automatically (`_stage_rollups`, site_tree template `cell_data`).
- The scaffolder prints a STALE nav block (old page labels) — use the results-first labels from
  the previous experiment's nav block instead.

## 2. Author the seven pages

Base each page on the previous experiment's version (reviewer-hardened structure), rewrite the
number-bearing content from the STAGED data only. Non-negotiables learned by review:

- Every quoted number carries a `<small>` source line naming the committed `data/` file.
  If a number's source file is not staged, STAGE IT through the pipeline (declare in
  `site_tree.py` TEMPLATES + resolver-accessor staging in `ingest.py`) — never cite a file
  that lives only on the run drive, and never write artifact FILENAME literals in ingest,
  macros, comments, or docstrings (the `test_runtree_consumption` ratchet counts prose too).
- Floor-terms translations up front: what a batch is (one wave; N waves ≈ a workday), crew
  sizes, footprint (aisles/bins), catalogue size — and get the catalogue number from
  `SELECT COUNT(*)` on the inventory DB, not from memory.
- Model-scope disclosures, stated plainly, each with its symmetry argument: put-away labor
  unmodeled (both rules), inter-aisle walking unmodeled (both schedulers), restock-then-pick
  sequencing, one-picker-per-aisle task grain (and the two channels bracket the granularity
  effect), breaks/shifts absent, fixed crew, no cold-start SKUs, no manual holds.
- Floor-trust content: a CONCRETE rotation-fairness rule (not "the pilot decides"), where the
  reclaimed hours go, time-vs-strain honesty.
- The ask carries: WMS translation of each lever, success criteria (comparable-week definition,
  duration, target with margin), rollback trigger, and the restock-hours metric.
- A "sizing the prize" section: substitute-your-own-numbers arithmetic frames; never convert
  model hours to dollars yourself.
- Era/provenance note if the demand stream or measurement basis changed since the previous
  experiment; the commit line states it is the run's own `run_spec.json` stamp.
- **TRAP:** channel `config.json` totals describe the whole shared build; the channels operate
  disjoint regime partitions (per-channel bin counts come from `runtime_metrics.db`
  `regime_bins`). Do not claim per-channel counts "differ in config.json" — a reviewer will
  falsify it.
- **TRAP:** per-channel constants differ (`pick_intercept`, `cart_swap_coef`,
  `batch_mean_frac`) — state them symbolically with both channels' values, sourced to the leaf
  configs.

## 3. Deprecate the previous experiment (the four-edit pattern, commit `a84fafd`)

1. mkdocs.yml: previous experiment's nav block re-indents under "Earlier experiments
   (reference)" keeping its labels; new block takes the top slot; both nav comments updated.
2. `!!! info "Superseded — kept for reference"` banner atop the previous experiment's index.md.
3. docs/index.md: current-experiment section replaced; previous becomes a bullet under Earlier
   experiments; closing table note updated.
4. Caveat inversion: the previous experiment's "no caveat" note becomes a dated
   note pointing forward to the new baseline.

## 4. The stakeholder reviewer loop

Reviewers: the four project agents `reader-site-director`, `reader-operations-manager`
(managerial pair — veto weight), `reader-wms-engineer`, `reader-warehouse-worker` (advisory).
Per iteration:

1. `python -m mkdocs build --strict` (must pass before any review).
2. Spawn all four FRESH (new instances every round — context cleared by construction), in
   parallel, pointing at the rendered `site/experiments/experiment-N/**/index.html` pages
   (managers+worker: index/comparison/full-results[+overview]; engineer: all seven + the
   committed `data/` files for number spot-checks).
3. Triage: managerial BLOCKING items must be fixed; engineer/worker items fixed unless they
   conflict with managerial clarity. When a gap needs a stat or figure the pages do not
   carry, **invoke the `route-reviewer-finding` skill** — do not start building. It picks
   between four routes that cost wildly different amounts, first match wins:

   - **R1** an artifact already answers it (most "we need a number for X" findings are a
     STAGING gap) — no re-run, no code;
   - **R2** the view already renders and nobody staged it. Views are DERIVED from the
     quantity and the mark (`core/quantities.derive_views`), so "is there a percent
     version?" has a mechanical answer, and if it is in `EVAL_BY_KEY[key].views` the file
     is already on the run drive — one `figures.yml` entry plus a caption;
   - **R3** a new quantity from data the run already recorded — usually one `Quantity`
     entry and zero renderer code. Re-run the analysis and re-ingest;
   - **R4** it needs data the simulation never recorded. **Stop.** It cannot be
     backfilled; give the reviewer the two honest options the era gate prints.

   Whichever route: **no ad-hoc graphs, ever.** A new figure is a registered evaluation
   (figures.yml entry + `@evaluation` declaring its `family=`, `shape=` and `quantities=`
   — outputs land in the leaf's `figures/<family>/` folder with a view-prefixed basename,
   drawn through common/chartkit.py) or a declared whatif artifact (runschema writer); a
   new data file is a site_tree template + resolver-accessor ingest stage. So every FUTURE
   run generates the resource automatically and the documentation step is pure re-run +
   re-stage. `context/guards/experiment_guard.py --scan` enforces this mechanically: a PNG
   with no declared producer in a current experiment is a finding.
   Before the stakeholder rounds, run one `analysis-sme` round (the analytical SME agent)
   against a rendered leaf whenever the figure set changed — it audits legibility, missing
   views, and unanswered analytical questions at the chart level, which the persona
   readers are not equipped to catch.
   **Run it against a FULL-SCALE leaf, not a toy fixture.** A small fixture renders every
   chart shape correctly but degenerates the data: at four batches nothing reaches
   significance, arms come out bit-identical, and churn is exactly zero — and the SME
   reports each of those as a finding, correctly, because from inside one leaf it cannot
   tell a fixture artifact from a result. Every such claim must be checked against the
   real run's tables before you fix anything; in the Experiment-8 round, two of the
   fourteen findings ("every uniform arm is identical", "MaxClu ties MinClu") evaporated
   against the 34-arm cell, where all 17 uniform arms differ. Re-analysing one real cell
   costs minutes and is the cheapest way to keep the loop honest.
4. Show the user each round's verdicts and the fix mapping. Rebuild, next round.
5. Converge when the managerial pair reports NO BLOCKING GAPS in the same round; cap at 4
   rounds and surface residuals to the user instead of iterating past the cap.

**Known loop behavior:** fresh reviewers DRIFT — late rounds surface new demand classes (e.g.
financial translation) rather than re-checking old fixes, and reviewers will falsify any prose
claim against the committed data, including claims introduced by earlier fixes. Budget one
extra fix pass after the final round for exactly these. Engineer number-audits passing
("every number matched the source") is the signal the data layer is done; remaining rounds are
about framing.

## 5. Gates + commits

```bash
python -m pytest Tests/architecture -q     # site_tree pins, figure registry, ratchet, ingest
python -m mkdocs build --strict
python context/guards/docref_guard.py --scan
python context/guards/path_guard.py --scan
```

Commits on develop (no push unless asked — **pushing docs/** deploys the site publicly**):
(1) pipeline/product changes (site_tree, ingest, any new evaluations), (2) experiment content +
deprecation + home page, (3) infra/chores. Then architecture- and memory-maintainer passes.
