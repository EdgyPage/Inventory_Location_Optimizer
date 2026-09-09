# Split the missed-share clause into supply and labour

Type: task
Status: open
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
