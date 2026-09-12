# Build the site analysis stage

Type: task
Status: open
Blocked by: 21

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
[Re-scope the analysis surfaces to the site](07-rescope-the-analysis-surfaces.md) settled the
rule and said its builds "cannot be written before the coupled unit builder produces a pair of
leaves to read" — that exists now
([Build the coupled work unit](18-build-the-coupled-work-unit.md)). What still blocks it is
[Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md): the
`_site/` tree has nothing in it until one dock writes there, and a site stage reading an empty
scope reports zeros rather than refusing (memory `a-grant-is-not-an-output` — the `[access]`
summary reports INPUTS, so only the `[render]` run summary says whether anything was written).

## Question

Build 07's answer: the site stage and `SiteContext` itself, the `yard` family's move to a third
scope value (its `schema_id` bump and the four test ties), the site clause in `equilibrium.py`
with the report's two-leaf accumulation, the rollup's `ValueError` refusal and its `analyze_run`
skip, and the two unlisted leaf surfaces — `series.py`'s `yard_overage_total` and
`throughput.audit`'s undeclared door read.

**04 section 7 comes due here, and it is now collectable.** The coupled put clause reads ONE
site number: both leaves' `_put_seconds` over `crew x day_seconds x days`, banded against the
SUMMED expectation — which sits at rho rather than below it, because
[Build the site put-away pool](19-build-the-site-putaway-pool.md) removed the cause of
`expected_utilization`'s "single-channel leaves undercut rho" caveat. Retire that caveat for put
on coupled runs and keep it verbatim flag-off. The per-leaf realized numbers stay visible beside
it: they are how the pool's fairness rule is seen working. The receiving clause takes the same
shape from 21's crew.

Two traps this stage sits on top of:

- **Consume run-tree levels POSITIONALLY** (CLAUDE.md section 3). A site scope is a new level
  shape, and `run_channel_rollup.py` must refuse a coupled run rather than sum two leaves whose
  savings are no longer additive.
- **A crew share denominated on span over-reads ~3x** (memory `calendar-span-is-not-work-days`);
  a leaf's span is its distinct `work_day` count, and the site's is the same count because one
  batch is one site day.

## What proves it

- **The site number equals the two leaves' sum**, asserted rather than assumed — the whole
  argument for one site clause is that `expected_utilization` is linear in load.
- **The rollup refuses a coupled run and its exception TYPE is the one 07 section 4 chose**;
  mutation-checked, because a refusal nothing tests is a refusal that gets caught by an
  `except Exception` somewhere upstream.
- **Uncoupled analysis output is byte-identical, MEASURED** — an existing archived cell
  re-analysed before and after, not just a canary.
- Nine verifiers, including the run-tree contract (`--sync` before the DDL edit, `--accept`
  after: the `yard` family's scope move is a schema change and rides the pipeline, never a
  consumer edit). Nine-verifier + `Tests/architecture` baselined against a `git archive` copy.
