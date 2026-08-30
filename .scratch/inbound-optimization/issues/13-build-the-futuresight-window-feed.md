# Build the futuresight window feed

Type: task
Status: open
Blocked by: 05, 14

## Question

Build decision 6 of "Define the inbound objective" (10) — the plumbing only; the policy
entries that read it are the arm roster's (05). The pieces:

- A `SpaceView` slot of its own, never merged into standing demand: `inject_demand`
  REPLACES `demand` by charter and `predicted` projects from exactly that field, so a
  merged window would silently redefine "Predicted clear" and break the one-batch-deep pin
  (`Tests/unit/test_space_timeline.py`). Shape: the flat per-batch `{sku: qty}` dicts of
  `batches[i+1 .. i+w]`, copied — never the shared pickle objects (the shared-pickle
  mutation hazard is documented at the injection site in the driver).
- Fed at the existing standing-demand injection site in `strategy_runner` — one slice of
  `batches`, no new import edge (Inbound already receives plain dicts).
- No fourth version counter: the window changes in the same event that bumps `demand_v` and
  is a pure function of the batch index, so it shares `demand_v` — the three-counter
  contract from the space-timeline design (03 §5) stands, and the cache ticket (06)
  composes keys unchanged.
- Clamp at the end of the script; an empty window is an empty slot, not an error.
- The script is REQUIRED: when `batches` is None (fingerprint miss → inline sampling), a
  run with any futuresight arm refuses loudly at startup — never silent inline window
  sampling (refusal-until-clean, the effort's own precedent).
- The window knob (name, per-arm declaration, `w` denomination in batches) lands with the
  arm roster (05) — build to whatever it names; blocked on it for exactly that reason.

## Comments

2026-08-29, from resolving "Name the policy arms and their knobs" (05): the knob is
`INBOUND_FUTURESIGHT_BATCHES` — int, `'all'` = the oracle w=∞ (a string sentinel that
survives a run spec), default None = inert; the arm refuses loudly when the knob is unset
or the precomputed script is missing. Scope change: this ticket now ALSO builds the
`futuresight` registry entry itself (`gain_forecast` over the window slot, both
registries), so it is additionally blocked by "Build the gain evaluator and the gain-plan
arms" (14), which supplies the evaluator the entry calls. The feed half is unchanged.
