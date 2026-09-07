# Narrow the drained clause to labour

Type: task
Status: resolved

Graduated from [Choose the coverage floor](15-choose-the-coverage-floor.md), decision 7,
amending [Declare the equilibrium bands](04-declare-the-equilibrium-bands.md). AFK build.

## Question

A day DRAINS today when nothing was cut and `standing_put + standing_dock + standing_carry`
is zero, and `standing_carry` sums all three `unpicked_*` reasons. The two SUPPLY reasons
(`unpicked_unavailable`: the bin held less; `unpicked_unstocked`: no bin held the SKU) are stock
not delivered, which the `missed_share` clause already owns; counting them as standing work
means no finite stock level can ever drain a day under lumpy lines (ticket 09: 0/20 drained
with pickers in band and every task realized).

Make the drained judgment LABOUR-ONLY: no cut, no put or dock standing, no `unpicked_daycut`
carry. The supply carry stays recorded in `carryover` and reported; the `shift_days` ledger
gains the split (`standing_carry_labour` / `standing_carry_supply`, or equivalent) so the
report can show both. `equilibrium._drained_clause` reads the labour figure; its docstring and
`simconfig/equilibrium.py`'s header say why. Decision 04's text on the map is amended by this
ticket's pointer, not rewritten.

Done when: the ledger row and the clause carry the split (a schema change on the sim DB rides
the pipeline); a unit test feeds a day with supply carry only and sees it DRAINED, and a day
with daycut carry only and sees it CAPPED; the throughput audit's `days_capped` follows the
new judgment; the pre-charge/era archives are unaffected (the ledger exists only under the
era).

## Answer

LANDED 2026-09-06. One definition of "drained", and it is labour-only.

**The definition.** `Optimization/simconfig/equilibrium.is_drained(cut, standing_put,
standing_dock, standing_carry_labour)`: nothing cut, no put or dock work standing, no
`unpicked_daycut` carry. The supply carry is not an argument -- there is nothing a caller could
pass that would cap a day on the shelf's account. `strategy_runner._shift_close_out` writes the
ledger's `drained` with it; `_drained_clause` READS that column and never re-derives it, so a
pre-split vintage's verdicts stand as its runner judged them (two ways to compute one quantity
is how they drift).

**The split.** The runner's carry fold already knew every unit's cause; it now keeps two
running totals beside the merged `_carry_now` (`_carry_labour` from `sim.carried`,
`_carry_supply` from `sim.unmet` + the shortfall), and the ledger snapshot is a 4-tuple
`(put, dock, labour, supply)`. `standing_carry` is unchanged (= labour + supply) so nothing that
read it moves; `standing` (the mixed level) still sums all four parts, because "was anything
standing" is still the first question -- the verdict just reads the labour part.

**The schema.** `shift_days` gains `standing_carry_labour` / `standing_carry_supply`
(sim_db `487a65bf83a9` -> `798778f4fae1`, adopted through `--sync` / `--accept`; the outgoing
id named in `known_ids` with its window). The pre-split vintage is served by a
`shift_day_frame` override that reads the two halves as NULL -- unknown, never 0 -- and
`frames._sdf` carries them as NaN. The `shift_days` CAPABILITY stays at the ten columns every
vetted shape has (the gate insists); the halves ride the named query only. `sim_semantics`
tags both as PIECES levels.

**The clause.** `_drained_clause`'s reading gains `supply_standing_days`,
`supply_standing_max_units` (a level: max, never a sum) and `supply_split_recorded`;
`summarize` prints "N with supply carry standing". `days_capped` follows the new `drained`
with no edit of its own, and the audit's per-day frame carries both halves.

**Tests** (1756 unit green; schema and column-semantics gates green): `is_drained` refuses each
labour term alone and has no supply parameter; a supply-only day is DRAINED and reported, a
daycut-only day is CAPPED; a pre-split ledger (None off the loader, NaN off a frame) reads as
"split not recorded" with its verdicts intact; the ledger round-trips the 11-tuple; a file
faked to the old vintage (dropped columns, re-stamped, WAL checkpointed) binds `stamped` to
`487a65bf83a9` and reads NULL halves through the override; the runner's source is pinned to
call `is_drained` and never re-derive.

**The check of the build:** a 6-day era run on a 300-SKU mixed catalogue (2 pickers per
channel, `fifo`, both leaves; no traceback, 56 analysis jobs, both audits rendered). The
fulfillment leaf closed days 2-5 DRAINED with 4 / 11 / 3 / 6 units of supply carry standing --
every one of which the old rule would have called CAPPED -- and labour carry 0 throughout;
the store leaf drained all six days with nothing standing. The pre-charge and era archives are
untouched: the ledger exists only under the era, and the reference-run passes of
[Take the reference run](09-take-the-reference-run.md) (the only `487a65bf83a9` files) still
bind through the override.

**Glossary:** *Standing work* amended in `CONTEXT.md` (demand the shelf could not serve is
missed share, not standing work). Decision 04's map line and ticket carry the amendment
pointer. Trap recorded as a memory: a test that fakes a vintage in place must checkpoint the
WAL and close before the loaders' immutable open sees it.
