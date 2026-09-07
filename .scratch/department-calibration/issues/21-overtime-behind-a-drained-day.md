# Overtime behind a drained day raises the instrument

Type: task
Status: resolved

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

## Answer

LANDED 2026-09-07. Overtime caps a day.

**The definition.** `equilibrium.is_drained` gains a fifth required term, `overtime`
(`last_finish > cap_end`, START-gate overtime): a day whose last task finished after its cap is
labour that did not fit the day, and it caps the day exactly as standing work does. The runner
computes the cap first and passes the stamp (`_overtime = float(last_finish) > _cap_end` in
`_shift_close_out`); the ledger keeps `last_finish` and `cap_end` as it did; `timeline.shift_end`
needed no change (it already ended such a day at the cap -- which is why the store leaf's day 3
read `end_s == cap_end` with `drained = 1`). The released-late clause is untouched: "lag behind a
drained day" stays the instrument bug it was built to catch.

**The read side -- the one judgment call.** The amendment moves no column, so the sim_db id
(798778f4fae1) does not move and no per-vintage `dataset.override` can separate a ledger stamped
before it from one stamped after. Both stamps the term reads are on every row, so the
`shift_day_frame` named query (and its pre-split override) serves `drained` as
`CASE WHEN last_finish > cap_end THEN 0 ELSE drained END` -- the definition's own stamp read off
the row, never a re-derivation from the labour terms; a no-op on a ledger the amended runner
wrote. Every consumer reads through `load_shift_days` (`equilibrium.check`; the audit's `_sdf`
frame via `requests.py`, hence `days_capped`), so the clauses, the frames and the quantity follow
with no edit of their own. 18's "reads the column, never re-derives" stands with this one stated
exception, written into `is_drained`'s docstring, the DDL comment, the `shift_days` capability
caveat, `sim_semantics`, the vintage comment block and the equilibrium header.

**The re-read** -- `comparison_20260906_222118`, both arms per leaf, through `equilibrium.check`
on the sim DBs with the run spec's staffing record:

| leaf | 17's check | now |
|---|---|---|
| store | audit RAISED (day 4: 138.1 s behind "drained" day 3) | no raise; 36/40 capped (35 + day 3), 36 overtime days, 4 drained early; day 4's lag recorded behind capped day 3 |
| fulfillment | 39/40 capped | 39/40 capped, unchanged (its one drained day had no overtime) |

The reviewer noted that "declared throughput not delivered" overstates a day that delivered
everything a few seconds late, so the clause reading gains `overtime_only_days` (capped, nothing
standing, last task past the cap) and the reason and summary name them: the store leaf reads
"36 capped (1 by overtime alone)" -- day 3; the other 35 left labour carry standing.

The store's other three clauses read as 17 reported them (pick 0.913 vs 0.980, put 0.291 vs
0.134, missed share 0.205 trending +0.156 against 0.095) -- those belong to
[Let a base-stock top-up reach the shelf](20-let-a-base-stock-top-up-reach-the-shelf.md).

**Tests** (`Tests/unit` green; the schema, column-semantics and dataset gates green; `--sync`
moved no shape): `is_drained` refuses overtime alone and the parameter has no default; a day
that ended AT its cap with a late last finish and nothing standing is CAPPED and the lag behind
it is recorded, not raised; a day that ended early is DRAINED; the pre-amendment stamp handed to
the check RAW still raises (the contradiction the amendment removes); a ledger stamped before the
amendment reads its overtime days capped through the loader, on the current vintage and through
the pre-split override alike, and `_sdf`'s `capped` follows; the runner's source is pinned to
compute `_overtime` before the verdict and pass it.

**Glossary:** *Standing work* gains the overtime sentence in `CONTEXT.md`. 18's map line and
`equilibrium.py`'s header carry the amendment. The architecture layer is re-synced by its
maintainer in the chore commit that follows.
