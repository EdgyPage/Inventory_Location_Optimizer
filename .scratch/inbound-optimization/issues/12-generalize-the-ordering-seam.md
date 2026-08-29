# Generalize the ordering seam

Type: task
Status: resolved

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

## Answer

Built 2026-08-29. All code lands in `Inbound/priorities.py` — `Inbound/transit.py` and
the manager needed ZERO edits, which is the resolution's core finding: `bounded_order`
was already the one seam every ranking crosses (`yard_order`, `dock_order`, the v1
global path, both local paths), so teaching IT to resolve both entry kinds leaves every
caller kind-blind by construction.

- **The tag.** A new `ordering` decorator sets `fn.ORDERING = True`, probed via getattr
  — the `STANDING` idiom. The registries and accessors are untouched code; they hold
  and resolve either kind, exactly as the ticket asked.
- **The branch.** `bounded_order` checks the tag. KEY entries take the exact former
  body — the seeded `'fifo'` entries did not move a byte. An ORDERING entry composes
  with the bound BOUND-FIRST, per the ticket's letter: ONE call per drain, on a COPY of
  the `bound` longest-waiting candidates, the remainder following in arrival order —
  never re-run per pick. Inert exactly when the proposal is arrival order, the same
  degenerate case that keeps fifo keys inert.
- **The loud guard.** The entry's return must be a permutation of what it was handed
  (same objects, compared by id); anything else raises ValueError. A silently dropped
  trailer would stand in the yard forever and a duplicated one would stage twice —
  neither with an error — so this is a CLAUDE.md §3-class silent trap closed at the
  seam rather than left to arm authors.
- **The purity pins are tests** (`Tests/unit/test_trailer_pipeline.py` §3b, plus a
  registry end-to-end in `test_standing_yard.py`): the generalized path reproduces
  key-sort on the seeded fifo entry element-for-element (stamp tie, no-stamp infinity
  case included); every bound is inert on yard-ordered input through BOTH branches;
  the bound bites correctly under a reversing proposal (bound=1 is strict arrival
  order, k_cap semantics); a mutating entry cannot corrupt the caller's list and the
  seam consumes no RNG; non-permutation proposals raise; and an `@ordering` entry
  registered under YARD/DOCK resolves through the accessors and reproduces the seeded
  fifo rankings via `yard_order`/`dock_order` on real machinery.

Full unit tier green: 1393 passed. The real ordering entries (gain plan, futuresight)
stay with the evaluator (04) and the roster (05), as scoped.
