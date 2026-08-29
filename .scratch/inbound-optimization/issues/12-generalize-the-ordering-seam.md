# Generalize the ordering seam

Type: task
Status: open

## Question

Build decision 4 of "Define the inbound objective" (10): a yard/dock registry entry may be
an ordering function `(candidates, ctx) -> ordered list`; pure keys stay the degenerate
case. The pieces:

- `Inbound/priorities.py`: `YARD_POLICIES` / `DOCK_POLICIES` accept both entry kinds; the
  accessors resolve either; `bounded_order` composes with both (bound the candidate set
  first, then order — inert under fifo exactly as today).
- `Inbound/transit.py` `yard_order` / `dock_order`: key entries key-sort exactly as today —
  the seeded `'fifo'` entries must not move a byte (byte-identity by construction); an
  ordering entry is called once per drain on the frozen ctx and its list is consumed
  front-first by same-drain refills, the existing deque contract, no mid-drain re-score.
- Purity pins: an ordering entry mutates no manager state and consumes no RNG (the
  frozen-`ctx` contract); a test proving the generalized path reproduces key-sort on the
  seeded fifo entries.

The real ordering entries (the gain plan, futuresight) are NOT this ticket — they arrive
with the evaluator (04) and the arm roster (05). This is the seam only.
