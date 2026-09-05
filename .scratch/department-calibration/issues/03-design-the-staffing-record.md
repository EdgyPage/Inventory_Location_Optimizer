# Design the staffing record

Type: grilling
Status: open
Blocked by: 01

## Question

The shape of the committed record — the artifact this map exists to produce — and its
seams. Three sub-decisions:

**(a) Split shape: one site pool dealt across departments, or independent per-department
counts?** The charter says "calibrate the crew split", but the model prices crews
differently — machine order-pickers (store), human walkers (fulfillment), forklift
drivers (pallet put-away), receivers with no travel term — so a fungible pool would
assert cross-training the cost model cannot price.

**(b) The record itself.** What one block declares, per channel × department: headcounts,
the shift length it is sized against, expected throughput (units/day), the duty-cycle
band from [04](04-declare-the-equilibrium-bands.md)? Where does it live and what pattern
does it follow? The survey (assets, *Put crews and the config seams*) settles the
patterns: `CONFIG['global']` keys + a call-time spec accessor returning a picklable dict
(the `recv_crew_spec` pattern) — explicitly NOT the `put_crew_spec` pattern, which reads
module settings directly with no CONFIG key and is the repo's documented
accepted-and-ignored-forever trap (`sim_config.py:566`). Prefer ONE spliced key list (the
`INBOUND_KEYS` precedent) over retyping keys at every seam. Five seams (declare, CONFIG +
accessor, CLI, run-spec record + BOTH restore sites, `workunits._shared`) plus the
adjacent sixth when an evaluation reads it: stamp onto sim_result in
`_sim_result_from_meta` (memory: `config-is-not-a-channel-to-an-evaluation`).

**(c) Scope riders.** The departments in scope are pickers, put-away (base crew + split
family), and receiving; the yard jockey stays unbudgeted (inbound 01's decision: staging
is not crew labour) and the reloader stays out (`norsl`). The `put_crew_spec` trap fix
(PUT_CREW_SIZE/MODE gaining real CONFIG keys and seams) rides this record's build rather
than living as its own effort. Confirm or amend both.

**Recommendation:** (a) independent counts chosen jointly — the "split" is the
calibration's OUTPUT, not a fungible pool; report the site total per channel as a derived
line. A true shared-pool tradeoff (a picker added is a receiver removed) needs cross-crew
pricing this model does not have — if wanted, it is a later effort started from this
record. (b) as sketched: one spliced-list record, `recv_crew_spec` pattern, expected
throughput declared beside the crews. (c) both as stated.

## Comments

2026-09-05, from resolving [Define the calibrated era](01-define-the-calibrated-era.md):
(a) is settled by 01 — pickers per channel are the declared input, the put crew and receiving
crew are SITE totals derived from them; no fungible pool. The record therefore carries INPUTS
(per-channel pickers; ρ_pick / ρ_put / ρ_recv; f_put / f_recv; the intercept scales and
per-item ratio; the measured `s_pick` / `s_put` with provenance) and DERIVED OUTPUTS (batch
content per channel, put crew, receiving crew, expected throughput), both in the run spec,
computed live at setup so no run can carry a stale literal. (b)'s pattern choice stands. The
picker seam build is [Build the picker staffing seam](07-build-the-picker-staffing-seam.md),
blocked on this record's shape.

2026-09-05, from resolving [Choose the calibration procedure](02-choose-the-calibration-procedure.md):
two records now exist and must not be confused. The COMMITTED calibration record (constants +
provenance, under `Optimization/simconfig/`) is an INPUT this record reads; the run-spec staffing
record designed here COPIES the constants it ran under together with their `provenance`
(`seed` / `measured` / `derived`), and carries two stamps 02 decided: `calibration_stale`
(catalogue fingerprint differs from the record's) and the `K_max` exceedance flag (declared
pickers above the heaviest-aisle bound). Both are things an evaluation will read, so both take
the sixth seam onto `sim_result`.
