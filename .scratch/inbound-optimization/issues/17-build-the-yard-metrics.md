# Build the yard metrics

Type: task
Status: resolved
Blocked by: 07, 09

## Question

Build everything [Define the yard metrics](07-define-the-yard-metrics.md) decided — one
session, one schema change. The raw material (`YardTransit.stamps`, `freeze_ctx`) exists
since the standing-yard build (09).

- **The two sim_db tables.** `yard_trailers` (grain: trailer): `seq` LABEL(id),
  `arrived_s`/`staged_s`/`emptied_s` STAMPs (s, sim clock), `status` LABEL
  (`done`/`discarded`/`standing`); null-meanings — `staged_s` NULL = never staged,
  `emptied_s` NULL = standing at run end (censored detention). Includes the run-end flush
  of the standing yard and staged remainders so censored trailers get rows.
  `yard_drains` (grain: drain, 1:1 with batches): `batch` LABEL(id, pairs with
  `batch_stats`), `yard_start`/`free_doors_start`/`yard_end`/`staged_remainder_end` LEVELs.
  Both always created; rows only when the standing yard is on.
- **The seven pipeline steps**: DDL + declared shape; family vintage (append the outgoing
  id, `--sync`/`--accept` — `schema-maintainer` owns the contract); the writer stamp
  already rides `create_run`; semantics tags in `sim_semantics.py` in the SAME commit (the
  completeness ratchet fails otherwise); `Requires` optional-fill (new columns are outside
  the guaranteed surface by definition); named queries beside the family (no consumer SQL);
  the `'yard'` `SIM_CAPABILITIES` key.
- **The report surface**: the tenth figure family `yard` (leaf scope, charter "did the yard
  bind, and what did the inbound policy cost in trailer-days") plus its run-tree contract
  glob; `FRAME_TABLE` extensions (trailer, drain); the five declared Quantities —
  `yard_overage_days`, `yard_over_threshold_trailers`, `yard_detention_days` (+ its
  fixed-mark distribution figure), `yard_depth` (series-elsewhere), `binding_cuts` — all
  capability-gated `'yard'`, stance level, direction lower; the no-direction scorecard
  evaluation (door utilization, the contention pair, the censored share) via
  `SEMANTIC_USES`; and the two ungated availability quantities in `throughput` —
  `missed_pieces` (carryover reasons `unpicked_unstocked` + `unpicked_unavailable`, never
  `unpicked_daycut`) and `missed_share` (of `items_demanded`).
- **Derivation altitude is fixed**: raw stamps only in the DB; spans, detention days,
  overage, and utilization derive at analysis with the run's recorded
  `INBOUND_FEE_THRESHOLD_DAYS` (HEAD-default fallback when a spec predates recording);
  seconds→days at the analysis units seam only.
- **The knob**: `INBOUND_FEE_THRESHOLD_DAYS = 2.0` (shared with the arms build, 14 —
  whichever lands first creates it), calibration rule in its docstring; CLI flag and
  run-spec recording deferred to the first sweep, per the family precedent.

## Comments

2026-08-31, from resolving "Design the phased funnel" (08): the deferred run-spec recording
above now has an owner and a deadline. 08 decided the first sweep, and the wiring graduated
as [Build the run-shape layer](18-build-the-run-shape-layer.md), which records
`INBOUND_FEE_THRESHOLD_DAYS` and restores it at BOTH sites (`_apply_run_spec` and
`run_analysis._apply_run_shape` — separate functions, and today neither touches inbound).

Worth stating plainly because this ticket's derive-late decision depends on it: until 18
lands, a fee re-report resolves the threshold through the HEAD-default fallback named
above, which means it silently uses **this checkout's** default rather than the run's. The
fallback is correct for runs that predate recording; the campaign must never exercise it,
so 18 is a prerequisite of phase 1 rather than of phase 2.

Two items 08 settled that land on this build's surface: `yard_overage_days` DOES earn a
`headline` slot beside hours and missed share (the campaign's question is "and at what fee
cost", so the fee is half the decision) — a distinct number in its own unit, never summed
with hours and never a ratio against them, added at the END of `HEADLINE_ORDER` alongside
the entries from [Build total production hours](19-build-total-production-hours.md). And
`binding_cuts` is load-bearing earlier than expected: it is one of the two acceptance
criteria for the pilot gate, read as an ABSOLUTE value under `fifo`, so a campaign cannot
start until this build reports it.

## Answer

BUILT 2026-08-31. Everything ticket 07 decided is code, plus one thing 07 could not have
known was attached to it (see THE ERA GATE below).

**Two schema events, both through the pipeline.** sim_db `ce01ca0095b2` -> `be2a593727be`
(`--sync` before the DDL edit, `--accept` after; the outgoing id was already in `known_ids`
so nothing needed adopting, and it gained the vintage comment it had been missing — the
whole standing-yard build ran on that shape and recorded no yard measurement at all).
Run-tree `d37f33da7229` -> `51f99901f03c` for the `figures_yard_pngs` glob.

**The two tables** are as specified, with one addition: `status` is a FIFTH element on
`YardTransit.stamps`, stamped by whichever of `discard`/`door_freed` the trailer left
through, rather than re-derived downstream from the null pattern. Today `discarded` and
`never staged` are the same nulls, so a derivation would agree with the column — and would
stop agreeing the day a staged trailer is dropped. `standing_stamps()` is the censored tail
and is deliberately NON-destructive beside `drain_stamps()`, which is.

**The run-end flush has its own writer** (`save_yard_trailers`) and is NOT inside the final
`if pb:` checkpoint block. That block only fires when a batch window is unflushed, and the
checkpoint cadence is `max(1, n_batches // 10)` — so at any batch count divisible by the
cadence there is no such window, and a censored tail written inside it would be lost on
exactly those runs. Those are the rows an adversarial ordering concentrates its overage
into. Pinned by an e2e test that fails when the flush is stubbed out.

**Derivation altitude held.** Nothing in the DB is a span, a day or a fee; `frames._ydf`
takes the raw stamps, the run end (the censoring bound, `max(batch_start_time + duration)`)
and the threshold. A test drives the same rows under two thresholds and gets two fee
totals with no simulation in between — that property is the reason the columns are shaped
this way, so it is asserted rather than described.

**The report surface**: family `yard` (tenth overall, ninth at leaf scope) with four
evaluations — `yard.fee`, `yard.detention` (ranked mean + a bespoke box-per-arm
distribution with the threshold as a fixed mark, so `lifo`'s CONCENTRATION is visible where
a mean would read as a tie), `yard.binding` (depth over drains + the cut count, carrying
the pilot gate's own contention sentence in its subtitle) and `yard.scorecard` (an
`inspection` mark: door utilization, the contention pair and the censored share, none of
which has an honest DIRECTION and none of which is therefore a Quantity). Plus
`throughput.missed` for the two availability quantities. All four render — proven on a real
standing-yard run through `run_analysis`, asserting the FILES and an empty error tally,
because a grant is not an output and a declaration is not a render.

**Door count is derived, exactly**: `free_doors_start = doors - staged`, and a run's first
drain always freezes with nothing staged, so `max(free_doors_start)` IS the door count
whenever that drain is in the frame. On a resumed arm it is a lower bound, making
utilization an upper bound — printed in the table rather than assumed away.

### THE ERA GATE — this ticket's quantities were the first to name a capability

`core/era.py` and `Tests/architecture/test_data_era_gate.py` carried a test written to FAIL
on the day any quantity named a capability, with the design for the missing half in its
docstring. The yard's five and demand service's two did it, and the test fired exactly as
written. Built what it specified, because leaving it red was not an option and the gap is
real: the static rule is a statement about the QUANTITY TABLE, and a vetted vintage can
carry a conditional table with no rows in it (measured: `reorder_queue` has rows in 68 of
166 arms). The only symptom would have been a figure that silently does not appear.

- `EvalContext.capabilities()` — `capability.probe` per arm, INTERSECTED not unioned (a
  comparison figure draws every arm on one axis, so a capability one arm lacks is one the
  figure cannot use; a union would license a chart that silently omits an arm).
- `requests.EraUnmet`, a subclass of `Denied` so every caller's truth-test still works, but
  distinguishable — the two call for DIFFERENT actions. A denial says a file was missing
  (re-run the stage); this says the file is present and older than the measurement (re-run
  the sweep, or report the degraded form). One bucket printed the wrong instruction.
- `era_shortfall` checks what is GATED before probing, so an ungated evaluation never opens
  a connection to learn nothing — asserted by a test with an exploding context.
- A fourth tally bucket, an `[era]` driver line and an `[era] run summary` beside
  `[access]`, at INFO: on every archived run the yard is unanswerable, and that is the
  correct outcome rather than a fault. The `yard`/`missed` REQUESTS also return `EraUnmet`
  rather than `Denied` when the tables are present but empty — `yard.scorecard` declares no
  quantity to be gated on (its read-outs have no direction, so none may be a Quantity) and
  would otherwise have landed in the access bucket alone, telling a reader to fix a working
  pipeline.
- `Schema` sweep: `GATED_CONSUMERS` as a SECOND category beside `DECLARED_CONSUMERS`, not
  an exemption inside it. An exemption would weaken the one invariant ("every declared
  consumer is version-free") for every consumer in the repo; a second category is held to
  a different and equally checkable rule — outside the surface, non-empty, every gap named
  by a real capability. A `Requires` in neither still fails the wiring test.
- `era.QUANTITY_READS` split into it plus `GATED_READS`, along the same line.

### Decisions and deviations worth carrying forward

- **`yard_overage_days` does NOT have its headline slot yet.** 08 granted it one at the end
  of `HEADLINE_ORDER`; a slot there reads a STEADY-STATE scalar out of the series document,
  and the yard's numbers are per-trailer and per-drain. Adding the panel now would put an
  empty sixth panel on every inbound-off publish, which is every run in the archive. The
  comment naming the debt is in `HEADLINE_ORDER` itself, and a note is on
  [19](19-build-total-production-hours.md), which is already opening the series builder.
- **A trap found in the frame vocabulary**: `stats_core._metric_series` is written as
  "batch, ELSE the task frame", so a new `FRAME_TABLE` kind would be silently looked up in
  `df_t`, found absent, and returned as an empty Series — reported downstream as an
  unmeasurable quantity rather than a misrouted one. `PER_BATCH_KINDS` now names the three
  kinds that can pair batch-for-batch, `metric_specs()` filters to them, and a kind in
  neither list raises.
- **`Source.db_also`** was added for `missed_share` alone: its numerator is a `carryover`
  fold and its denominator is `batch_stats.items_demanded`, and declaring either alone
  would have hidden a real read from the era gate. `db_reads` keeps its single-pair shape
  (every consumer unpacks it); `all_db_reads` is the complete answer.
- **`carryover` needed its own capability, named query and loader.** 07 called the
  availability quantities "ungated", meaning not YARD-gated; they are still outside the
  guaranteed surface, so the era gate forces a capability and got one.
- **The conditional-reader detector had a blind spot**: it found loaders by their inline
  `SELECT`, so a loader routed through the named-query registry — now the PREFERRED route —
  was invisible to it. Widened to read the module's own `Query(name=, sql=)` registrations
  and match functions that name one. Still source-only, so it cannot share a bug with the
  registry it checks.
- **The fee threshold falls back to this checkout's default** and says so in the log, until
  [18](18-build-the-run-shape-layer.md) records it. Unchanged from this ticket's own
  comment; the campaign must never exercise it.
- `INBOUND_FEE_THRESHOLD_DAYS` already existed at 2.0 — the arms build (14) landed first,
  as 07 point 10 anticipated.

### The nine gates

All nine green, INCLUDING the derived architecture layer — regenerated here rather than
left owed a fourth time (09, 13 and 15 each deferred it, and the debt was compounding). The
four-step chain ran clean and `--catalog-merge` produced **no `purpose: TODO` at all**: it
derives each entry from the module docstring, so the new package documented itself into the
catalog. `render_html --build` was run twice per the case-only-rename rule.

One PRE-EXISTING failure remains and is not this ticket's:
`test_bin_mutation_sites::test_the_dead_site_is_still_dead` substring-matches the two
`StorageCart.add_from_bin` PROSE CITATIONS in `Inbound/trailer.py` — both present in HEAD,
untouched here, already spun off while resolving 16.

The memory mirror (`context/memory/store/`) is deliberately NOT in this commit. The push
picked up two memories that a previous session had left drifted, and bundling someone
else's pending sync into this commit is the thing CLAUDE.md §5 warns against; the
`memory-maintainer` owns that push. The new memory itself
(`run-end-writers-miss-the-final-flush`) is in the live store.
