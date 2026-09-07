# Narrow the drained clause to labour

Type: task
Status: open

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
