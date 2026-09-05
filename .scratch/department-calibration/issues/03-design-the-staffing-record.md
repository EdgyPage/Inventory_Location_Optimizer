# Design the staffing record

Type: grilling
Status: resolved
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

## Answer

Resolved 2026-09-05. (a) was settled by 01 before this session opened (no fungible pool; pickers per
channel are the one declared input; put and receiving crews are derived site totals), and (b)'s
pattern stood (CONFIG keys + call-time accessor + one spliced key list, never the `put_crew_spec`
trap). Seven remaining shape decisions were put to the user in one round and every recommendation
was confirmed.

1. **Pickers live in two flat global keys**, `store_pickers` and `ff_pickers`, on the spliced
   `STAFFING_KEYS` list; the compile-time constants become their settings defaults and
   `CONFIG['channels'][<ch>]['num_pickers']` becomes a call-time read of them. A per-pick-config
   `num_pickers` override that DISAGREES with its channel's declared count **raises at setup**: the
   script is derived from the declared crew, so an arm fielding a different crew is the stale-literal
   trap 01 killed, restated per arm. A module that restates the channel value (as the committed ones
   do) stays legal. This supersedes 07's "arm-level escape hatch" line.
2. **Two blocks, one record.** `staffing_spec()` in `sim_config` returns INPUTS only (pickers, the
   ρ_pick / ρ_put / ρ_recv and f_put / f_recv scalars, the intercept scales and item ratio, and the
   put crew mode). The derivation is a **pure module**, `Optimization/simconfig/staffing.py`: inputs +
   the loaded calibration record + the script's totals in, the derived dict out (batch content per
   channel, put crew, receiving crew, expected throughput — 01's table is its interface). It runs at
   setup after batch precompute, because the receiving crew needs the packs the script implies and the
   script is itself derived from pickers. The run spec records one `staffing` key with `inputs` and
   `derived` sub-blocks. Derived values are never CONFIG keys, so none can be set from the CLI.
3. **Restore reads the record and re-derives.** On resume the recorded `derived` block is
   authoritative (an arm fields the crew it started with) and a re-derivation from the restored
   inputs that disagrees **raises**; on re-analysis the same disagreement **warns and stamps**. The
   re-derivation is a free drift check for a changed derivation or calibration record.
4. **Legacy crew knobs under the era are an error.** `--recv-crew-size`, the base put crew size and
   the split family's three crew counts stay live flag-off (byte-identical discipline) but are
   derived under the era; passing one explicitly under the era raises. `put_queue_split` on under
   the era also raises for this map: single queue only; per-stream sizing joins the out-of-scope
   swept-axis effort, which is why 02 records per-stream `s_put` as diagnostics.
5. **One five-valued provenance enum, shared by both records:** `assumed` (a settings default nobody
   chose), `declared` (set by flag or spec), `seed`, `measured`, `derived`. The calibration record's
   constants are copied onto the staffing record with theirs; scalars carry `assumed` or `declared`;
   the derived block carries `derived`. A value keeps its provenance as it is copied.
6. **The sixth seam carries the whole record**: `_sim_result_from_meta` stamps the entire `staffing`
   dict (inputs, derived, and the `calibration_stale` / `K_max` stamps) onto `sim_result` as one
   key, so the fogged throughput audit graduates without touching the seam again.
7. **Scope riders confirmed as stated:** the `put_crew_spec` trap fix (PUT_CREW_SIZE / PUT_CREW_MODE
   gaining real CONFIG keys and every seam) rides the build; PUT_CREW_MODE stays a DECLARED input on
   the record (the derivation sizes the count, the mode is a labour-model term the cost model
   prices); the yard jockey stays unbudgeted and the reloader stays out.

**Glossary:** `CONTEXT.md` gained **Staffing record** (Day-over-day) and **Provenance** (Measurement),
2026-09-05. No ADR: none of the seven is hard to reverse.

**Map consequences, applied this session:** [Build the picker staffing seam](07-build-the-picker-staffing-seam.md)
is unblocked and its override line is superseded (comment);
[Build the derivation, the calibration record, and the era wiring](08-build-the-derivation-and-era-wiring.md)
now has its module boundary, restore semantics, error cases and provenance enum (comment);
[Declare the equilibrium bands](04-declare-the-equilibrium-bands.md) learns the audit reads the
stamped record (comment). The throughput-audit fog entry is sharpened but stays fog until 04 closes.
