# Give put-away a per-channel expected travel

Type: task
Status: open

Graduated 2026-09-08 from [Re-read the check under the fielded floor](25-re-read-the-check.md),
failure 1. AFK build. Skills: `codebase-design` (this changes what the derived record's put block
means), `domain-modeling` if a glossary term moves.

## Question

Make the put-away department's expected utilization a per-channel quantity, the way picking's and
receiving's already are.

**The defect, measured.** `simconfig/staffing.py` `derive` prices each channel's put-away load as
`ch_put_s = t.per_day(t.put_units) * s_put` -- per-channel UNITS times ONE site-wide `s_put`
(41.24 s/unit on the reference pair: an expected travel from the aisle mouth over the class-uniform
destination, plus handling at the class-mean height). The two sections do not share that geometry.
25 measured **98.50 s/unit realized on the store and 29.15 s/unit on fulfillment, a 3.4x spread.**
The consequence is that the `utilization` clause fails on BOTH leaves in OPPOSITE directions
(fulfillment 0.477 realized against 0.691 expected, store 0.362 against 0.155) while the site total
is very nearly exact: 1,425,557 realized s/day against a derived 1,437,958, **-0.9%**.

**What is NOT wrong, and must not move.** The crew SIZE. `put_crew = crew_size(put_load_s, S,
rho_put)` sums the site load and is right to: 25 measured the units per channel within 2% of the
script (6,241/day store, 27,814/day fulfillment) and the total load within 1%. A change that
re-sizes the crew on this pair has broken something rather than fixed it.

**The shape of the fix**, from the two departments that already get this right:

- `s_pick` is derived PER CHANNEL and stamped per channel (108.371 store / 17.362 fulfillment on
  this pair -- a 6.2x spread the derivation already respects).
- Receiving takes its per-channel seconds STRAIGHT FROM THE SCRIPT (`ch_recv_s =
  t.per_day(t.recv_s)`), exact because an unload has no travel term; its `s_recv` is a reported
  average, not a price anything is computed from. Receiving is in band on both leaves.

Put-away is the only one of the three departments that multiplies per-channel units by a site-wide
constant, and it is the only one out of band. The closed form that produces `s_put` already knows
the section's geometry -- it is computed over a warehouse whose aisles the channel determines -- so
the question is whether to derive it per channel from that same expectation
([Derive the expected-travel closed form](13-derive-the-expected-travel-closed-form.md)) or to take
the per-channel seconds from the script the way receiving does.

**The one decision inside this ticket**: what the record keeps stamping at the SITE level.
`put.s_put` is currently a single `constant(...)`; after this change a site value is at best a
weighted average and at worst misleading. Either it becomes a per-channel map beside the
per-channel expectations, or it stays as a reported average carrying a note that nothing is priced
from it (receiving's `s_recv` is the precedent for the second). Do not leave a site-wide `s_put`
that a future consumer could price something from.

## Done when

- Each channel's put load is priced at that channel's own expected seconds per unit, and the record
  stamps it per channel beside `put.expected_utilization`.
- The put crew size is unchanged on the reference pair (a test pins it), because the site load is
  unchanged.
- Any new input rides all five seams (memory `config-knob-has-five-seams`), and the restore/drift
  check (`derived_differs`) covers the new record shape.
- Flag-off is byte-identical; the derivation stays pure and still runs after precompute.
- ONE 40-day era re-read on the reference pair shows put utilization IN BAND on both leaves. On this
  pair that means expectations landing within `band_tol` of a realized 0.477 fulfillment and 0.362
  store -- the realized numbers are not in question, the expected ones are.
