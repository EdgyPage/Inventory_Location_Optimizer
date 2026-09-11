# Re-scope the analysis surfaces to the site

Type: grilling
Status: resolved
Blocked by: 03

HITL. Skills: `grilling`. Blocked by
[Design the site scope in the run tree](03-design-the-site-scope-in-the-run-tree.md): every surface
here reads whatever scope that ticket creates.

## Question

Three analysis surfaces assume a channel leaf owns its inbound. Decide what each becomes.

1. **The yard views.** `Optimization/Performance_Evaluations/yard/{binding,detention,fee,scorecard}.py`
   (`binding.py:83-92`, `detention.py:112-117`, `fee.py:74-99`, `scorecard.py:41-86`,
   `__init__.py:30`) are per arm within one leaf, reading trailer and drain frames out of that
   leaf's DBs and denominating door utilization by `_arm_span_days` (`scorecard.py:45-54`). With one
   site yard the frames belong to a site scope and the span denominator is ambiguous between two
   leaves' clocks — a scope change, not a regroup. Memory `calendar-span-is-not-work-days` is the
   live trap: denominate on distinct `work_day` values, not a calendar span, or a utilization
   over-reads ~3×. Memory `a-right-site-total-hides-two-wrong-shares` is the second: check the
   per-channel split separately, because a correct site total can hide two bands failing in
   opposite directions.

2. **The equilibrium report's receiving clause.** `Diagnostics/equilibrium_report.py:49-81` reaches
   a verdict per `(cell, pair, config, channel)` leaf via `expectations_for(staffing, pair=,
   channel=)` (`equilibrium.py:332`); the receiving and put clauses (`:390-397`) already compare a
   leaf's load to a site crew. Coupled, evaluate the receiving clause **once at site scope**, and
   decide the same for put (ticket 04 owns the number, this ticket owns the report). Memory
   `equilibrium-check-two-traps` applies: `released_late` belongs to the CAPPED day before it, and
   the self-check must re-price rows rather than average them.

3. **`run_channel_rollup.py`.** The charter settles the outcome — **refuse a coupled run, keep the
   script for inbound-off runs**. What is open is the mechanics: `run_channel_rollup.py:11`,
   `:116-153`, `:180-226` states independence as its validity argument, so decide what it reads to
   detect a coupled run (ticket 03's marker), what it says when it refuses, and whether the
   docstring's argument is rewritten or simply scoped.

4. **The figure views.** Figure views are derived from shape and quantities (memory
   `figure-views-are-derived`), so a site-scoped quantity may render a view nobody declared. Decide
   whether any new site quantity needs a declared view here or whether that waits for the campaign —
   and route any new quantity through the declaration, never an ad-hoc renderer (the
   `route-reviewer-finding` skill is the precedent).

Starting map of seams: [`../../inbound-optimization/assets/site_dock_sizing.md`](../../inbound-optimization/assets/site_dock_sizing.md)
§3.

## Answer

**Two of this ticket's four premises were false, and the false ones were the expensive ones.**
Figure views are not at risk — `derive_views` does not read scope — and the receiving self-check
§2 asks to preserve **was deleted**, along with the driver that called it. What is real is the
scope machinery itself: there is no scope→context dispatch anywhere, so `site` is not a string
that can simply be declared.

### 1. The site context, and where its stage runs

**A third flat-pool stage in `run_analysis`, one job per `(pair, arm-pair)`, on a new
`SiteContext`.** `run_analysis` has exactly two stages today — `config` then `aggregate`
(`run_analysis.py:455`) — and `analyze_run` adds the dossier stage. A site evaluation needs both
leaves of one pair under one arm pair, which is neither.

**`SiteContext` populates the same `_by_key` shape `EvalContext` uses, and that is the whole
design.** Every frame broker keys on `s['db_path']` and `s['run_id']` and nothing else
(`requests.py:158`, `:176`), so yard frames bind the `_site/` DB and batch frames bind each
channel's leaf DB, and **every existing broker works unchanged**. The site-ness lives in what the
keys point at, not in a second implementation of the brokers.

**Rejected: an elected leaf reaching sideways into `_site/`** — 03 already rejected it for storage
and it is no better for rendering. **Rejected: `RunContext`** — it has no `_by_key`, no
`capabilities()`, no schema-identity gate (`context.py:446-484`), so every broker would need a
second implementation, and the per-pair parallelism is lost to a serial parent stage.

**Found while deciding — a bare `scope='site'` renders nowhere, silently.** There is **no
scope→context dispatch table**: contexts are hard-coded per stage (`run_analysis.py:156-168`,
`analyze_run.py:130`). Worse, `requests.py:601` reads
`scope = ev.scope if ev.scope in ('aggregate','run') else 'config'`, so an unrecognised scope is
**silently namespaced as config**, and `artifact_map.config_dirs()`/`run_dirs()` (`:146`, `:171`)
prepare no directory for it. That is `a-grant-is-not-an-output` with no log line at all. The
unvalidated-scope half is already 11's; the request-namespace fallthrough is new and joins it.

### 2. The two denominators

`scorecard` deliberately holds two read-outs that do **not** share a denominator
(`scorecard.py:65-72`). Coupled, both become ambiguous, and they resolve differently.

**Door utilization takes the SITE calendar span** — `min(start)` to `max(end)` across both leaves'
batch frames, replacing `_arm_span_days`' single-leaf read (`scorecard.py:45-52`). A door is
occupied on the site's calendar whatever channel's packs sit behind it. The union is well-posed
rather than a mixture of two axes because 02 and 04 put `recv_clock` and `put_clock` on one site
origin.

**Receiver busy takes both leaves' `recv_seconds` over distinct `work_day`**, unchanged in form
from `_receiver_busy` (`:86-92`) and still **not** the calendar span —
`calendar-span-is-not-work-days` is the live trap and it already printed 184% once. The leaves
share days by construction: `staffing.py:715-727` refuses to sum channels that ran different
batch counts.

**The per-channel shares are printed beside the site number, never instead of it.**
`a-right-site-total-hides-two-wrong-shares` is exact about why: put-away's site load was 0.9% off
while both per-channel bands failed in opposite directions. A site total is not evidence for the
split, and the split is the only place 04's fairness rule is visible.

**Found while deciding — the per-trailer frame is already built against a leaf frame.**
`yard_frame` passes `_arm_end_s(ctx, key)` as the censoring bound (`requests.py:165`), and
`_arm_end_s` reads `batch_stats` (`:139-150`). So detention and fee — the two evaluations that
look purely site-denominated — are coupled to one leaf's clock at frame-build time. Under a site
yard that bound is the **site** end, the same union as door utilization. This is why the yard
family moves whole rather than splitting: there is no subset of it that reads only site data.

### 3. The equilibrium report: where the site number is assembled, and what the leaves keep

**The report assembles it; `equilibrium.py` stays a pure rows-in library.** `check`/`check_rows`
are per-DB (`:1225-1242`) and `_utilization_clause` receives exactly one leaf's `work_rows`
(`:860-897`) — there is no path that hands it two. And the module states its own discipline:
*"No CONFIG, no settings, no run tree — the same discipline `staffing.py` keeps"* (`:113-116`).
So the report — already the only half that walks the tree (`rt.channel_runs()` at `:74`) —
accumulates both leaves' rows and calls a new pure
`eq.site_utilization_clause(rows_by_channel, summed_expectation)`.

**Rejected: a second DB argument on `check`.** It puts the run tree inside the library.
**Rejected: banding in the report alone.** It duplicates the arithmetic in a second place, which
is the drift `equilibrium_report.py:20` exists to prevent ("the two cannot disagree").

**Site bands, leaves report.** On a coupled run the leaf verdict keeps `labour`, `supply`, `rework`
and `pick`, and **drops put and recv from its banded clause**; the site verdict — one per
`(cell, pair, arm-pair)` — carries put and recv once, banded against the summed expectation
(04 section 7). The two per-channel realized shares ride **inside** the site clause as unbanded
readings. Keeping both banded would publish two verdicts on one crew that can disagree with each
other — the double count reappearing as a reporting artefact after being fixed by construction.

**The coupled marker costs nothing here.** `resolver.py:67` keeps the parsed layout on the
resolver and `equilibrium_report.py:69` already calls `resolver_for`, so `rt.layout.get('coupled')`
is zero new I/O and — because it never spells the filename — zero new budget in
`Tests/architecture/test_runtree_consumption.py`.

**`released_late` needs nothing.** Its `d-1` attribution lives only in the clause (`:833-856`);
`frames.py:69` sums it per day without shifting, and both stay per leaf under coupling.
`equilibrium-check-two-traps` trap 1 is untouched by this ticket.

### 4. The rollup's refusal — and the exception type is the decision

The charter settled the outcome; the mechanics turn on one fact nobody had looked at:

**`analyze_run._step:36` catches `Exception`, and `SystemExit` is not one.** `run_channel_rollup`
is called from inside the cell loop (`analyze_run.py:74-75`), so a `SystemExit` refusal — the
house style for a CLI shape refusal, and what `run_restock_selection.py:265-272` uses — would
**abort `analyze_run` mid-run**, killing every remaining cell's analysis and the cross-cell what-if
stage. `run_restock_selection` gets away with it because nothing calls it from the hub.

**So: `rollup()` raises a `ValueError`**, matching this file's own in-library precedent
(`_baseline_entry`, `:110-113`), which `_step` catches and logs per cell. **And `analyze_run`
skips the rollup step outright on a coupled run**, so the log reads `skipped: coupled run` rather
than `analysis step failed` once per cell — a failure line on every cell of a healthy run is how
a reader learns to ignore failure lines.

Detection is `rt.layout.get('coupled')`: the rollup takes a **cell** dir and already roots
`_tree_for` one level up at the run root (`:64-66`), so the marker is in hand.

**The docstring is rewritten, not scoped.** The independence claim is asserted three separate
times — `:3-13` ("Because the channels are independent, absolute savings … are ADDITIVE"),
`:179-182`, and the stdout line at `:249-250`. A caveat pasted above a docstring that still says
the opposite three times below is not a fix. The argument is not deleted either: it is restated as
conditional on the inbound-off model, which is what the whole published archive is.

### 5. Three surfaces the ticket did not list

**(a) `yard_overage_total` is in EVERY arm's series doc.** `needs=('series',)` transitively loads
`yard_trailers` for every arm (`requests.py:271`), and `series.py:115` folds the sum in;
`headline/top_vs_baseline.py:339` then declares `yard_overage_days` as an optional quantity. A
**headline** evaluation carries a site quantity today. This is the more dangerous of the two,
because moving the yard rows to the `_site/` DB breaks a field in every series document rather
than in one folder. **In scope.**

**(b) `throughput/audit.py:292` prints `ctx.dock_ceiling()`** — door-derived — on the recv row
while declaring `needs=('shift','batch')` (`:325-328`), i.e. it reads yard-denominated data
through an undeclared dependency. **In scope.** Note it cannot simply declare `needs=('yard',)`:
the yard request is DENIED on every inbound-off run, which would stop the audit rendering on the
entire archive. The site door count reaches it another way or the read moves.

**(c) `Diagnostics/receiving_report.py` check 3 — "the uid blocks are disjoint" — is a per-leaf
assertion about a crew that is about to be site-wide.** 04's `first_uid = max(k_pickers)` makes a
putter uid mean the same person in both DBs, which is the opposite of disjoint across leaves.
**Graduated**, with section 7, into
[Reconcile the site crews' work stream](15-reconcile-the-site-crews-work-stream.md): it is
write-side reconciliation, not an analysis surface, and its answer depends on a uid allocation 04
specified but did not build.

### 6. Views are safe; the family scope is what is pinned shut

**§4's premise is inverted. `derive_views` never reads scope** (`quantities.py:803-824`): it is a
function of the mark's `comparable`/`paired`/`fixed`, the quantity's `stance`, `unit.kind ==
'share'` and its suppressions. The five quantities the yard family already declares keep their
exact view sets when their evaluations move. A site quantity rendering an undeclared view cannot
happen.

**No new quantity is declared in this ticket.** Per `route-reviewer-finding`, R3 is reached when a
reviewer asks for a number; inventing one now would declare a view for a figure nobody has asked
to read.

**`FAMILIES['yard']['scope']` becomes `'site'`, a third value.** The two scope namespaces are
independent — `figures_subdir` returns `figures/<family>` whatever the family scope
(`families.py:122-127`) — so leaving it `'leaf'` looked free. It is not: `LEAF_FAMILIES` feeds
`_LEAF_FIGURE_FAMILIES`, which generates the glob
`{cell}/{pair}/{config}/{channel?}/figures/yard/*.png` (`schema.py:167-168`). Leave it and the
contract declares a path nothing produces while the real output under `<pair>/_site/figures/yard/`
is **undeclared** — a preflight violation.

The move costs, precisely:

- `SITE_SCOPE_FAMILIES` beside `LEAF_FAMILIES`/`RUN_SCOPE_FAMILIES` (`families.py:84-88`).
- `_SITE_FIGURE_FAMILIES` + its glob in `runschema/schema.py`, and `yard` out of
  `_LEAF_FIGURE_FAMILIES` (`:108-109`) — which **moves `schema_id`**, the bump 03 budgeted.
- `Tests/unit/test_chartkit.py:387` widened to the three-way enumeration and `:391` made a
  three-way union. `:391`'s `RUN_SCOPE_FAMILIES == ('cost',)` stays true and is not touched.
- `Tests/integration/test_runschema_contract.py:542` (the ordered tie) and `:552` (disjointness)
  extended to the third tuple.

### 7. The self-check section 2 asks to preserve does not exist

`reference.recv_exact_check` is **gone**: `Optimization/simconfig/` has no `reference.py`,
`unload_price_for` returns zero hits repo-wide, and `equilibrium.py:78-83` records the retirement
of the driver that used it as a precondition. Its loader survives **orphaned** —
`load_receive_events` (`Picking_Data.py:3048`) and the `receive_event_frame` query (`:1711`) have
zero callers. `staffing.py:833-839` already carries a comment correcting a *previous* stale claim
about this same check, which is how often this one has been got wrong.

**The rebuild is out of THIS ticket and graduates** into
[Reconcile the site crews' work stream](15-reconcile-the-site-crews-work-stream.md), beside
section 5(c): both are write-side reconciliation and both need the site crews to exist. What 07
does is leave the finding on the record and **fix the memory**, because a live loader with no
callers is exactly how a dead feature hides for months — and `hand-run-test-tiers-rot-silently` is
the same failure in a different tier.

### Written in this session

- `CONTEXT.md` — **Clock** amended. It said *"a receiving stamp is taken from the site's clock, a
  pick or put stamp from its own channel's"*; 03 wrote that and 04 then moved `put_clock` to the
  site day origin, and nothing had touched the file since (`534d6f88`). Put now reads from the
  site's clock; pick stays per channel. Not coining a term — repairing a published line a later
  decision falsified, on the ticket whose surfaces report those stamps.
- `context/memory/store/equilibrium-check-two-traps.md` — trap 2 corrected: it named
  `reference.recv_exact_check` as the live self-check. The re-pricing **discipline** is still
  right and still the reason an average is wrong; the **module** is deleted and the loader is
  orphaned. A memory that names a file has to be checked against the file.

### What this hands onward

- **[Reconcile the site crews' work stream](15-reconcile-the-site-crews-work-stream.md)** — new,
  takeable now, a decision rather than a build: what `receiving_report`'s uid-disjointness check
  becomes under one site crew, and whether the deleted re-pricing check returns at site scope or
  its orphaned loader goes.
- **[Harden the three positional seams](11-harden-the-positional-seams.md)** gains a fourth: the
  `requests.py:601` namespace fallthrough that silently reads an unknown scope as `config`.
  Same class as the unvalidated scope string already folded in there, same silence.
- **The builds** join the map's existing fog patch: the site stage and `SiteContext`, the yard
  family's move with its `schema_id` bump and four test ties, the site clause in
  `equilibrium.py` + the report's two-leaf accumulation, the rollup refusal and its `analyze_run`
  skip, and the two unlisted leaf surfaces (a) and (b). None can be written before the coupled
  unit builder produces a pair of leaves to read.
