# Build the lead distribution

Type: task
Status: open
Blocked by: 02

## Question

Build the seeded per-trailer lead distribution exactly as
[Choose the lead distribution](02-choose-the-lead-distribution.md) resolved it — the
resolution there is the spec; this ticket adds only the build obligations.

- The rename (`INBOUND_LEAD_MINUTES`, was `INBOUND_TRAILER_LEAD_MINUTES` — the CONFIG key
  `inbound_lead_minutes` already matches, so `Optimization/config/settings.py` plus the one
  mapping line in `sim_config.py` change) and the new `INBOUND_LEAD_SPREAD` (σ,
  dimensionless, default 0.0).
- The two loud contradictions in `inbound_spec()`: spread > 0 without
  `INBOUND_STANDING_YARD`; spread > 0 with a zero median.
- The seq-keyed stateless draw at trailer creation in `TrailerTransit.dispatch`
  (`Inbound/transit.py` — the draw replaces the single `lead_s=self.lead_s` argument):
  `lead_i = median_s · exp(σ · Z_i)` via `default_rng(SeedSequence([SEED_WORLD, TAG, seq]))`,
  one draw, discard. The build picks the `TAG` literal.
- σ = 0 constructs **no RNG** and runs the scalar path verbatim — prove byte-identity by
  test, not argument (the seam-test + lockstep pattern `Tests/unit/test_trailer_pipeline.py`
  already carries for this family).
- Determinism and keying tests: same seeds ⇒ same per-seq leads; a given seq draws the same
  lead regardless of how many trailers preceded it; heterogeneous leads make yard order
  differ from dispatch order and `YardTransit`'s `(arrived_s, seq)` sort honors it.
- CLI flag and run-spec recording stay deferred to the first sweep (ticket 09's precedent) —
  do not build them here.
