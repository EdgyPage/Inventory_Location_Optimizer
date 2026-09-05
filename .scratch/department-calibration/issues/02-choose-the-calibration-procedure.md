# Choose the calibration procedure

Type: grilling
Status: open
Blocked by: 01

## Question

Analytic, empirical, or hybrid — and which arm prices the empirical half?

The knowability split is exact (assets survey, `arm_invariance`): the batch script is a
pure function of (inventory, affinity, config, seed) shared identically across arms, so
per batch the SKU set, demanded units, and handling mass `sum(qty · handle_var(w,v))` are
computable with NO run. Receiving goes further: an unload has no travel term
(`Inbound/unload.py`), so the dock's whole demand is analytic. But WHICH bins serve the
demand — and therefore all pick and put-away travel, intercept counts, height multipliers,
cart swaps, and shortfall — is arm-dependent placement state: it needs a measured run.

So the choice is really about the empirical half:

- Which ARM prices it? (Travel differs per arm by design — that variance is the whole
  experiment.)
- What does the calibration run look like — depth, leaves, what it measures (the
  heaviest-aisle floor from 01, realized makespans per department, duty cycles)?
- What does the procedure RECORD? A calibration that keeps only its output counts cannot
  be audited or re-run when the catalogue changes.

**Recommendation:** hybrid, priced under `fifo` — the campaign's own reference and
negative control, and the arm the pilot already measured. Analytic first-cut from the
script (dock exact; pick/put handling mass exact, travel estimated), then ONE calibration
run under `fifo` in the 01 era to measure the travel-bearing makespans and set final
counts. Record the analytic prediction AND the measured value per department — their
ratio IS the travel share, and it is the number that lets the next catalogue re-calibrate
cheaply.

## Comments

2026-09-05, from resolving [Define the calibrated era](01-define-the-calibrated-era.md): the
question is RESHAPED. Calibration is now a DERIVATION from declared pickers (the chain is in
01's answer), not a search — so this ticket no longer chooses counts. It decides the REFERENCE
RUN that supplies the two measured constants, `s_pick` and `s_put` (seconds per unit picked /
put): which arm (the `fifo` recommendation stands), depth, leaves, and what it records — the
analytic prediction beside the measured value, their ratio being the travel share, as already
recommended — plus how the constants are declared with provenance so the next catalogue can
re-calibrate. Receiving needs no measurement (exact from the script). The heaviest-aisle floor
is still measured here, but it now BOUNDS the picker count a scenario may declare rather than
setting one. The run must be taken under the new cost model
([ticket 06](06-add-the-per-item-charge.md)) or it measures labour no later run uses.
