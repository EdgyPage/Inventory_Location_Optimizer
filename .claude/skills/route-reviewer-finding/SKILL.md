---
name: route-reviewer-finding
description: Decide how a stakeholder-reviewer finding becomes a real artifact — an existing file, a view that already renders, a new declared quantity, or a request the simulation cannot answer at all. Use inside the publish-experiment review loop whenever a reviewer asks for a number, chart or table the pages do not currently carry, and before writing any new renderer. Args - the finding, verbatim, and the run root it would be answered from.
---

# Route a reviewer finding to the thing that answers it

A reviewer asks for something the pages do not carry. There are exactly four ways that ends,
they cost wildly different amounts, and picking the wrong one is how a fifteen-edit
refactor gets started for a number that was already sitting in a CSV.

**Take the first route that matches. Do not skip ahead.**

The routes exist because `publish-experiment` §4.3 used to say one thing — "RE-RUN THE
PRODUCER" — which is right for exactly one of the four.

---

## R1 — an artifact already answers it. *No re-run, no code.*

Most "we need a number for X" findings are a **staging** gap, not a data gap. Look before
you build:

```bash
python -m Optimization.runschema.contract --report
```

Then read, in this order — cheapest and most likely first:

| Where | What lives there |
|---|---|
| `<root>/_dossier/` | the run dossier: cross-cell tables, the objective/rule catalog, the census |
| `<leaf>/tables/` | per-run summary, tidy batch metrics, `vs_baseline.csv`, significance tests |
| `<cell>/channel_rollup_summary.csv` | the podium and the savings the pages quote |
| `<root>/whatif_*.json` | day length, labor invariance, window comparisons |

Resolve every path through `Optimization.runschema.resolver_for(base_dir)` — never join
strings, and never cite a file that lives only on the run drive.

**If it is there:** stage it (a `site_tree.py` TEMPLATE plus a resolver-accessor stage in
`ingest.py`), cite it with its `<small>` source line, done.

## R2 — the view already renders, and nobody staged it. *No re-run, no code.*

This route did not exist before views were derived, and it is now the second thing to check
because it is nearly free.

A figure's views are **derived** from the quantity it draws and the mark it draws with
(`core/quantities.derive_views`), not chosen per module. So "is there a percent version of
that chart?" is a question with a mechanical answer:

```bash
python -c "from Optimization import Performance_Evaluations as P; from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY as E; print(E['layout.travel'].views)"
```

If the view is in that tuple, **the file already exists on the run drive**. It is not in
`figures.yml`. Add one entry plus a caption and re-ingest.

Two ledgers tell you when it does not exist and why, and both are printed by
`Tests/architecture/test_view_coverage.py`:

- `views_pending` on the evaluation — the view SHOULD exist and is not written yet. That is
  R3-shaped work, but small: the mark library already draws the shape.
- `views_suppressed` — the view must NEVER be drawn, and the reason is recorded. Quote the
  reason back to the reviewer. A percent improvement against a zero baseline is not a
  missing chart; it is thirty-four empty rows under a caption asserting a comparison.

`test_figure_registry.UNREGISTERED_STEMS` lists figures the suite renders that
`figures.yml` deliberately does not carry, with the reason for each. Check it before
concluding a figure is missing.

## R3 — a new quantity from data the run already recorded. *Re-run the analysis.*

Add one `Quantity` to `core/quantities.py`. Usually **zero renderer code**: declare the
source, the unit, the direction and the label, and the significance CSV block, the
`vs_baseline` row group, the effect panel and the derived views all follow.

```
Quantity(key=..., label=..., axis_stem=..., unit=..., direction='lower'|'higher',
         source=Source(per_batch=('batch'|'task_mean'|'task_sum', <frame column>),
                       steady_state=..., series=...))
```

Then, in this order:

```bash
python -m pytest Tests/unit/test_quantities.py Tests/architecture/test_data_era_gate.py -q
python -m pytest Tests/architecture/test_view_coverage.py -q
python -m Optimization.analyze_run <run_root> --workers 20 --granularity graph
python docs/experiments/ingest.py --source <run_root> --exp experiment-N
```

**The era gate runs first for a reason** — see R4. If it passes, the data is there.

Three things that will bite:

- If the frame column is DERIVED (`completion_rate` is `total_items / duration`, computed
  in `frames._bdf` and stored in no database), declare `db_columns=` with what it is
  actually built from. Otherwise the era gate checks a column that does not exist and
  reports a gap against a surface that was right.
- A new **series** quantity must be added to `SERIES_ORDER`, or nothing renders it and
  nothing says so. There is a check for this; it fires at import.
- Run the analysis as a **module** (`python -m ...`). A heredoc-launched pool hangs
  silently for the full run: every spawned worker dies re-importing `<stdin>`.

## R4 — it needs data the simulation never recorded. **Stop.**

The era gate is what tells you which side of this line you are on:

```bash
python -m pytest Tests/architecture/test_data_era_gate.py -q
```

A finding is R4 when the number would need a sim-DB column outside
`Schema.compat.guaranteed_surface('sim_db')`, or a table no run in the archive wrote.

**It cannot be backfilled.** Schema identity is derived from shape and archived files are
never rewritten; there is no migration that makes a finished run answer a question it did
not record. Give the reviewer the two honest options and let them choose:

1. a new sweep on a vintage that records it — state the cost in hours, and that every
   published number would move to the new run;
2. a recorded capability caveat and the degraded form — name the
   `Picking_Data.SIM_CAPABILITIES` key on the quantity, carry `capability.provenance`'s
   caveat into the caption, and say plainly what the degraded number does and does not
   support.

**The one exception, and why it is not one.** The map-precompute backfill worked. It looks
like a counter-example and is not: it wrote an **independent sidecar** keyed to the run,
computed from inputs the run already had. It did not add a column to an archived sim
database. If the finding can be answered from a sidecar computed off existing inputs, it is
R3 with an extra writer, not R4.

---

## After any route

- Never an ad-hoc graph. `context/guards/experiment_guard.py --scan` makes a PNG with no
  declared producer a finding, and it is the check that keeps this loop honest.
- Every quoted number carries a `<small>` line naming the committed `data/` file.
- Re-run the gates the route touched, plus:

```bash
python -m mkdocs build --strict
```

- Tell the user which route the finding took and what it cost. "R1, already in
  `vs_baseline.csv`" and "R4, needs a new sweep" are the two answers they most need to
  hear early, and the second one especially should never arrive at the end of a fix pass.
