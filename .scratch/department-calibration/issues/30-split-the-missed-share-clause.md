# Split the missed-share clause into supply and labour

Type: task
Status: resolved
Blocked by: 29

Graduated 2026-09-08 from
[Fit the store's window to its own steady state](27-fit-the-store-window-to-its-steady-state.md),
decision 9 and the instrument defect it found. AFK build. Skills: `codebase-design`.

## Question

`Optimization/simconfig/equilibrium.py` `_missed_share_clause` reads
`(items_demanded - total_items) / items_demanded` per batch. On the store leaf of
`comparison_20260908_094846` that difference is `unpicked_daycut + unpicked_unstocked` on all 40
days, and `items_demanded` includes `_pending` (the previous day's carry, all three reasons), so a
re-offered unit is counted again in both terms: cumulative `items_demanded` 312,302 against 255,817
fresh. The clause's docstring, `frames.MISSED_REASONS` and `throughput/missed.py` say it is
supply-only. It was reading labour overflow (0.037 -> 0.159) while the supply share it claims to
judge was flat at 0.070-0.072 against an expected 0.078.

**Build two clauses in place of `missed_share` and the strict `drained`:**

1. **`supply`** -- stockout units (`unpicked_unstocked` flow) over FRESH demand (script demand,
   not the effective batch), re-attempts counted once; LEVEL read against the record's `1 - fill`
   (now the solved floor's, ~0.025) with a declared tolerance; TREND as today (half-window means
   within `MISSED_TREND_TOL`).
2. **`labour`** -- the realized cut share (`unpicked_daycut` flow over fresh demand) within a
   declared tolerance of the record's STAMPED expected cut share (29), PLUS the standing labour
   carry bounded (below one day's cap) and its half-window means not trending. This replaces
   "every day drained": with a declared day cv of a third, 14 of 40 days exceed a full day's
   capacity at any headroom, so a strict drained count is not a property of equilibrium. `drained`
   survives as a READING (days drained, days capped) inside the labour clause, never as a verdict.
3. `released_late` keeps its own clause; `utilization`'s band is unchanged but its expectation now
   comes from 29's derived crew.
4. Re-align the docstrings, `frames.MISSED_REASONS`, `throughput/missed.py` and the audit tables
   with the split; the audit's `days_capped` ranking stays.
5. Note in `shift_days` (or its reader) that `standing_carry_labour/supply` are LEVELS that equal
   the day's flows only under one batch per day -- the clause must read the flows.

## Done when

- The check reports `supply` and `labour` as separate clauses with separate expectations, and a
  sabotage test proves each fails on a series the other passes (a pure-overflow series passes
  `supply`; a pure-stockout series passes `labour`).
- Re-read on the two existing 2026-09-08 runs (no new run): the store's `supply` reads ~0.070
  against 0.078 and PASSES; `labour` reads the overflow and FAILS, as the old instrument should
  have said. Both numbers quoted in the answer.
- The equilibrium glossary entry in `CONTEXT.md` still describes the instrument (27 amended it in
  advance; adjust wording only if the build deviates).

## Answer

LANDED 2026-09-09 (AFK build). The check judges the two causes apart, both as `carryover`
FLOWS over FRESH demand, each against its own stamped expectation; "every day drained" is a
reading. Re-read on the two 2026-09-08 runs without a new run: the store's supply share reads
**0.080 against 0.078 and PASSES**, its labour clause **FAILS on the overflow's trend**
(cut share 0.311 -> 0.130 across the halves, -0.181), as the old instrument should have said.

### What was built

1. **The flows** (`equilibrium.demand_flows`). Nothing records the sampled half of a batch, but
   the previous batch's whole pick-side carry (`unpicked_daycut` + the two supply reasons +
   `unpicked_notasks`) IS `_pending`, so `fresh_i = items_demanded_i - sum(carry_{i-1})` exactly
   whenever the carry is re-offered -- which the era forces (`roll_over_unpicked` completed to
   on). A carry larger than the demand raises `InstrumentError`. Supply re-attempts are counted
   ONCE per SKU as the rise in the family's failure count over the previous batch
   (`max(0, x_i - x_{i-1})`): exact for a stockout (the whole SKU demand fails), a floor for a
   partial fill. The cut is counted on EVERY attempt: a re-offered unit the whistle stops again is
   overflow again, the queue's cost. An empty `carryover` with a demand-pick gap is reported as
   UNRECORDED (pre-carryover vintage) and fails both clauses rather than passing at zero.
2. **`supply`**: level = sum(supply_new) / sum(fresh), a ratio of sums (the fill rate is one),
   within `SUPPLY_LEVEL_TOL` (0.02, typed) of the record's `1 - fill_rate`; trend as before
   (`TREND_TOL` 0.02, the old `MISSED_TREND_TOL`, now shared). Reported, not judged, without a
   stamped fill.
3. **`labour`**: (a) cut share = sum(cut) / sum(fresh) within `CUT_SHARE_Z` (2) sampling sds of
   the STAMPED guarantee's expected cut share -- the sd is a new closed form,
   `staffing.cut_share_sd` (`E[X^2] = sd^2[(1+z^2)(1-Phi(z)) - z phi(z)]`, validated by hand and
   against a seeded Monte-Carlo to 3%), over the window's day count, so the band is the declared
   law's and not a typed number; (b) the standing labour carry at each day's close (the day's
   LAST batch's daycut flow) under ONE day's capacity in units, `crew x S / s_pick` (the arm's
   re-centred `s_pick`); (c) the per-day cut share's half-window means within `CUT_SHARE_Z` sds of
   their DIFFERENCE under the same law (`_trend_tol`: the stamped sd, else the window's pooled
   within-half sample sd, else the typed number below four days; under one batch per day the
   share is the carry over fresh demand). A day the ledger never closed still
   fails it. The `drained` reading survives inside the clause -- days drained / capped /
   overtime-only / early, supply carry standing, and the ledger's own labour LEVEL as a
   cross-check of the flows (equal on every leaf read: 3,505 / 9,075). A capped day fails
   nothing.
4. `released_late` and `utilization` unchanged; `arm_expectations` now re-centres the expected
   cut share too (load and sd scaled by `s_arm / s_pair`; a cheaper unit is cut less).
   `expectations_for` gains `expected_cut_share`, `expected_cut_share_sd`, `guarantee`.
5. **Consumers.** The audit feeds the check `ctx.carry_df` (new `carry_frame` request over
   `frames._crdf`, the RAW rows) and its inspection table carries two more rows per arm --
   `supply share` and `cut share`: expected / realized / band / read, with "trending" and
   "carry past a day" named beside the band word; the subtitle names both expectations.
   `frames._cdf` / `throughput.missed` / `quantities.missed_share` keep their ARITHMETIC (per
   effective batch, re-attempts counted -- they serve every vintage and a run alone cannot say
   whether it re-offered) and their docstrings now say so and point at the check.
   `sim_semantics` (shift_days) and `load_shift_days` say the two carry halves are LEVELS equal
   to the day's flows only under one batch per day, and that the clauses read the flows (item 5).
6. **`Diagnostics/equilibrium_report.py`** -- the re-read, as a CLI over a finished run tree:
   walks the channel runs through the resolver, takes the run's OWN staffing record, re-centres
   per arm on the stamped `expected_pick`, prints `summarize` per arm; `--window LO-HI`,
   `--arm`, `--json`. 31 reads its run through it.
7. `CONTEXT.md` *Equilibrium*: the trend is on the cut share and the carry is bounded by a day's
   capacity; both shares are flows over fresh demand, a stockout on its first attempt, the cut
   on every attempt.

### Decisions the ticket left open

- **Both supply reasons**, not `unpicked_unstocked` alone: the fill closed form
  `E[min(q, Q)]` prices the partial fill (`unpicked_unavailable`, the bin held less) as a supply
  miss too, and `frames.MISSED_REASONS` already said so. One pair, `equilibrium.SUPPLY_REASONS`.
- **The labour band is derived, the supply band is typed.** The guarantee stamps a law
  (`load_s`, `sd_s`, K), so the cut share's sampling spread follows from it; the fill stamps a
  point and no spread over a finite window's SKU mix, so 0.02 is declared beside `TREND_TOL`
  (fog: a derived band for the supply level).
- **The trend is judged on the per-day cut share, not on the carry in units**: unit-free, one
  tolerance for both clauses, and identical to carry-over-fresh under the era's one batch per
  day. The carry in units is judged on its BOUND.
- **A capped day fails nothing.** Missing days do.

### The re-read (`comparison_20260908_094846`, days 20-39, fifo; the two fifo arms are byte-identical)

| leaf | supply level / expected | verdict | cut share (halves, trend) | carry max | pick util | clause verdicts |
|---|---|---|---|---|---|---|
| store | 0.0802 / 0.078 (+0.002) | ok | 0.2207 (0.311 -> 0.130, -0.181 vs band ±0.083) | 3,505 u = 0.53 day, day 24 | 0.963 / 0.851 | labour FAIL (trend), utilization FAIL (pick +0.11), supply ok, released_late ok, rework ok |
| fulfillment | 0.0817 / 0.078 (+0.004) | ok | 0.0440 (0.034 -> 0.032, -0.002 vs band ±0.056) | 9,075 u = 0.27 day, day 25 | 0.894 / 0.853 | PASS on every clause |

- The ticket's "~0.070" was the raw supply flow over EFFECTIVE demand (0.0704 here); the
  first-attempt flow over FRESH demand is 0.0802 (10,459 of 130,443 units; 1,567 re-attempts
  dropped from 12,026). Raw over fresh would read 0.0922 and fail -- the re-attempt rule
  matters. All three sit under the 0.078 band on this leaf; only the first is the definition.
- The store's labour level (0.2207) is NOT judged on these runs: their record predates 29's
  guarantee, so no expected cut share is stamped. The clause fails on the trend alone, with the
  carry inside a day (0.53 of 6,638 units).
- Over the whole run (days 0-39) the fulfillment leaf's labour ALSO fails, on a -0.061 trend
  (its ramp: 13,016 units of carry on day 6, 18/40 drained) -- the reason the measured window
  is 20-39.
- `comparison_20260908_075736` reads the same flows; only its utilization band differs (its
  record predates 26's per-channel put price: put 0.397 vs 0.155 store, 0.479 vs 0.691
  fulfillment), which is the record, not the instrument.

### From the review (code-reviewer, fixed before the commit)

- **The labour trend reused the typed 0.02** while a per-day cut share has the day law's spread
  (0.06-0.12 on the reference pair): a Monte-Carlo of twenty iid days at the stamped law failed
  26-52% of healthy windows on that term alone. The band is now derived like the level's --
  `CUT_SHARE_Z` sds of the difference of two half-window means, `sd * sqrt(1/n1 + 1/n2)`, with
  `sd` the stamped `cut_share_sd` or, on a record without a guarantee, the window's pooled
  WITHIN-half sample sd (so a real step does not widen the band that judges it). Pinned by a
  300-window Monte-Carlo test. On the 2026-09-08 pair the empirical spreads (0.093 store, 0.063
  fulfillment) land inside the law-implied range; `TREND_TOL` stays the SUPPLY trend's number.
- `supply_new` is a LOWER BOUND, not "the honest attribution": after a restock lands mid-shortage
  it charges the batch's failures to re-attempts first, so the level can read low against `1 -
  fill` on an under-stocked shelf. Per-SKU fresh demand is not recorded (only the batch total),
  which is what would make it exact -- a schema change, left in the fog as part of the derived
  supply band.
- `recorded` is now judged AFTER the reason filter (a table holding only put-side levels is not
  a recorded pick flow); a spread-less law reports rather than testing float equality; the
  report CLI resolves each arm's DB through `rt.leaf_path(cr, 'sim_db', ...)` rather than the
  leaf meta's machine-local `db_path`, and its docstrings no longer spell the two run-tree file
  names (the run-tree ratchet counts raw text).

### Verified

- `Tests/unit`: 1,879 green (the whole tier) before the review fixes; after them
  `test_equilibrium_check.py` (44), `test_throughput_audit.py`, `test_first_time_guarantee.py`
  (+1: `cut_share_sd` by hand and against a Monte-Carlo), `test_column_semantics.py` and
  `test_runtree_consumption.py` -- the last shows only its two PRE-EXISTING regressions
  (`expected_travel.py` 'warehouse.db', `supervisor.py` 'sim_meta.json'), neither touched here;
  chip spawned.
- `Tests/integration`: the six files that read `carryover` or the audit's frames, 60 green.
- Gates: path guard, docref, run-tree contract, context anchors, profile tree green; preflight
  ran both canaries (tree shape UNCHANGED, fingerprint refreshed -- the two
  `Optimization/schemas/run_tree/` files ride this commit as they did 29's).
- The architecture layer is stale (a rewritten module, a new Diagnostics CLI, new tests) -- the
  `architecture-maintainer`'s re-sync, as after every build on this map; the memory
  `nothing-is-lost-under-the-era` still says 30 is pending -- the `memory-maintainer` should
  amend it to LANDED with 31 outstanding.

### For 31

- Read each leaf with `python Diagnostics/equilibrium_report.py <run_root> --window 20-39
  --json <file>`; its run stamps a guarantee, so the labour LEVEL is judged there for the first
  time (band `2 * cut_share_sd / sqrt(20)`, ~0.03 on the store's law) and the trend band comes
  from the stamped sd rather than the window's own. The carry bound is `K * S / s_pick` units.
- The supply level is a lower bound (above); quote `units.reattempts` beside it, and if the
  level reads LOW against `1 - fill` on a leaf whose shelf is under-stocked, that is the bound,
  not a better shelf.

