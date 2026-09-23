# aisle-churn — a closed-form labour model, and how much churn an inbound decision needs

Label: wayfinder:map

Opened 2026-09-23 after the inbound-throughput map closed.  That map could not make unloading
order move pick labour: an inbound pack lands among 4-8 existing bins in the era run, and in a
40k fill even lifo sat inside the 0.24% noise floor with only 7.7% of the store's placements
re-picked within 40 batches.  The user's direction: **the aisles must churn enough to give the
bins breathing room for the inbound to make a decision.**  This map builds the maths that says
how much, as a reusable module, and verifies it against the simulator.

## Destination

1. A reusable closed-form module: an equation written once evaluates to numbers, renders to
   LaTeX, composes into models, and is held equal to the simulator code it mirrors
   (`Warehouse/kernel/closed_form.py`, the cost classes' `closed_form` attributes, the composed
   models in `Optimization/simconfig/models/`).
2. Closed-form answers, each verified by simulation within a stated tolerance or a named
   residual: where a SKU's equilibrium units come from; when reorders trigger and what lands;
   receive, put-away and pick labour per unit and per day, by placement rule and unloading
   policy.
3. The churn threshold over a 2-D grid of demand density x stock depth: where a placement rule,
   and separately an unloading order, first moves labour beyond the measured noise floor.
4. A visualiser and an analysis-schema extension (predicted vs realised per run), and the
   results published as an Artifact page.

## Decisions (the user's, 2026-09-23)

- **Both churn levers, as a 2-D grid**: demand density (`--store-demand`, `--ff-demand`) and
  stock depth (`--first-time-confidence`, which lowers the solved line floor).
- **40k sweeps plus the existing 400k roots** for verification; any 400k confirmation run is
  proposed at the end, for approval.
- **The maths as a reusable module**, attached to the pick and inbound classes where it is a
  per-event law and composed for the analysis where it depends on state.

## Whiteboard protocol

One file per session in `whiteboard/`: Question, Hypothesis, Derivation (rendered by the
module's writer where a model exists), Prediction (committed before measuring), Measurement,
Residual, Diagnosis (a named term), Revision, Next.  Never fit on the data that verifies.  Three
revisions without convergence opens an issue for the user.

## Sessions

- S00 anchors -- what the simulator charges, with code anchors.
- S01 sampler inclusion p_s vs line share pi_s.
- S02 levels (M1), and the knob check for the grid.
- S03 reorders (M2).
- S04 inbound flow and the yard (M3).
- S05 receiving and put-away (M3).
- S06 pick labour (M4), with the drain-order correction.
- S07 the marginal value of a location, g_b (M5).
- S08 churn on existing roots (M6 a-d).
- S09 retrodict the fill-trial null (M6 e-g).
- S10 registered grid predictions.  S11 grid runs.  S12 bisection.  S13 synthesis.

## Status

- **Milestone 1** (framework): `Warehouse/kernel/closed_form.py` + `Tests/unit/test_closed_form.py`.
- **Milestone 2** (cost laws as attributes): `PickConfig.closed_form`, `PutawayCost.closed_form`,
  `UnloadCost.closed_form` + `Tests/unit/test_cost_laws.py` (every registered config).
