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

## Amendment (2026-09-11, from ticket 21)

**THIS TICKET NOW CARRIES THE SECOND COMPARABILITY BREAK.** 21 built the coupled receiving
coordinator and correctly declined to measure a break, because the thing that breaks
comparability is a coupled run FIELDING one dock — and three things such a run needs were on
21's own exclusion list. Two of them are this ticket's:

- **The site-scoped rows have no home.** A coupled drain's `yard_drains` and `yard_trailers`
  are trailer- and door-denominated, so they are the site's and not a channel's. Their declared
  artifact is `<pair>/_site/inbound_<arm-pair>.db` (ADR-0005), and until it exists 21 PARKS the
  row rather than handing it to `leaves[0]` — writing it there would be
  `a-right-site-total-hides-two-wrong-shares` built deliberately.
- **One transit carries ONE `gain_bundle` slot for two owners** — that is 05's, and it is
  [Build the composite gain bundle](26-build-the-composite-gain-bundle.md), which runs
  alongside this ticket rather than before it.

So the run that fields the site dock is this one, and its acceptance gains what 21's could not
have: **measure the break per leaf and record it**, the way
[Build the site put-away pool](19-build-the-site-putaway-pool.md) did.
`Tests/e2e/test_coupled_unit_e2e.py::test_a_coupled_unit_matches_the_two_units_it_replaces` was
amended by 19 and left untouched by 21; extend it here.

**21's hand-off list for the driver, verbatim, so it is not re-derived:** build the dock, yard
and coordinator at UNIT scope the way `_build_put_pool` does; hand the SAME transit to both
leaves and `bind` each; chain the receiving uid block off the put pool's block end; base
`_recv_base` on `open_batch(day_of(i))` and report each leaf's finish to `note_records`; drive
`SiteReceiving.drain` INSTEAD of two `check_reorders` — phase 5 must not go back through each
leaf's own composition, or the earlier leaf's put drain runs before the site's receive; drain
the dock once and partition its records by owner (the six refused leaf accessors are the list of
what needs a site-scoped replacement); and drain `site_rows` every batch.

**The price decision is CLOSED and it is yours to build.**
[Decide the site dock's unload price](27-decide-the-site-docks-unload-price.md) answered: the
unload price is a statement about the MERCHANDISE, so the site dock holds a price LIST keyed by
the unloaded unit's own regime — the same shape 19 already tested one crew over, where `s_put`
is keyed by channel while the putters are one pool. So: `DockSpec`/`Dock` take a per-regime
mapping instead of one `UnloadCost` and `Dock.unload_seconds` resolves per unit at the charge
site, where 21's step-4 handoff already holds `regime_of(item.unit)`. Flag-off and uncoupled
construct NO list, structurally, in the `recv_crew_spec` style — an uncoupled leaf builds its own
dock from its own pick config exactly as today, which is what keeps every archived run
byte-identical. **That decision costs no comparability break**; yours is the one that does, and
it comes from fielding the dock, not from pricing it.

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
