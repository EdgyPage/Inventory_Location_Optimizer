# Overtime behind a drained day raises the instrument

Type: task
Status: open

Graduated from [Build the line floor](17-build-the-line-floor.md), whose 40-day check could not
render the store leaf's throughput audit. AFK build, amending
[Narrow the drained clause to labour](18-narrow-the-drained-clause-to-labour.md).

## Question

`equilibrium.is_drained(cut, standing_put, standing_dock, standing_carry_labour)` calls a day
DRAINED when nothing was cut and no labour stands. It does not look at the clock. On the
reference pair's store leaf, day 3 ended AT its cap (`end_s == cap_end`) with the last picker
finishing 138 s past it and nothing standing -- drained by the labour rule -- and the batch
released into day 4 was 138 s late. `_released_late_clause` treats lag behind a drained day
as the instrument contradicting itself and RAISES `InstrumentError`, so the audit rendered
nothing for the leaf (`throughput.audit failed: the batch released into day 4 was 138.1 s
late, but day 3 is recorded DRAINED`). 36 of the store's 40 days had overtime; under the
pre-18 rule the supply carry made every one of them CAPPED and the contradiction never
surfaced.

Overtime is labour that did not fit the day. Make the verdict say so: a day whose last task
finished after its cap is not drained (`is_drained` gains `overtime` -- `last_finish > cap_end`
-- or the runner passes it as a labour term), the ledger keeps `last_finish` as it does, and
the released-late clause's "lag behind a drained day" stays the instrument bug it was meant to
catch. State the amendment in `simconfig/equilibrium.py`'s header and 18's map line.

Done when: a unit test feeds a day that ended at its cap with a late last finish and nothing
standing and sees it CAPPED (and a day that ended early, DRAINED); the released-late clause no
longer raises on the store leaf of `comparison_20260906_222118` (re-read through
`equilibrium.check` on its sim DB); `days_capped` follows; the runner's `drained` column is
written by the one definition.
