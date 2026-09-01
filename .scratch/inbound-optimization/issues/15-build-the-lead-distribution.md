# Build the lead distribution

Type: task
Status: resolved
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

## Answer

BUILT, commit `50149dc`. Everything
[Choose the lead distribution](02-choose-the-lead-distribution.md) resolved is code, with
one design choice the ticket left open (the TAG literal) and one it did not anticipate
(a third refusal case). Unit tier 1448 green, e2e 22 passed / 1 skipped, integration 395
passed / 1 skipped.

**The knobs.** `INBOUND_LEAD_MINUTES` (was `INBOUND_TRAILER_LEAD_MINUTES`; the CONFIG key
`inbound_lead_minutes` already matched, so the rename really was two lines) and
`INBOUND_LEAD_SPREAD` (σ, dimensionless, default 0.0). Both threaded into `CONFIG` as
`_s.` references, which is what `test_no_tunable_in_config_is_still_a_bare_literal`
requires. `inbound_spec()` grew `lead_sigma` (dimensionless, crosses as authored) and
`lead_seed` (`seed_world()`, read at call time — never a snapshot) beside the existing
`lead_s`; the median is still converted minutes→seconds exactly once, at that seam.

**The TAG is `0x1EAD`** — a literal in `Inbound/transit.py`, documented as never derived,
because changing it re-rolls every lead schedule that was ever run. Named here because it
is the one thing the build got to pick and nothing else can re-derive it.

**Spread zero constructs no RNG, and that is what makes the archive safe.**
`TrailerTransit.lead_for(seq)` returns `self.lead_s` — the same float, not a rounded
recomputation — when `lead_sigma <= 0`, so the pre-spread expression is literally still
the expression that runs. Proven three ways rather than argued, per the ticket:

1. *the seam* — `lead_for` equals the median under `==`, not `approx`, at four seqs and
   three medians;
2. *the mechanism* — a booby-trapped `np.random.default_rng`/`SeedSequence` that raises on
   call is never reached across a five-trailer dispatch. This is the test that would catch
   a future refactor "simplifying" the guard into a σ=0 draw;
3. *the lockstep* — a manager fed `lead_sigma=0.0, lead_seed=987654321` produces the
   identical put-queue stream, ledgers, censuses, `snapshot()` rows and arrival deliveries
   as one built the pre-spread way, compared **drain by drain** over a three-drain
   scenario, never as aggregates. Its real target is the subtle failure: a seed that leaks
   entropy while the spread is nominally off.

**The draw.** `median · exp(σ · Z_seq)` via
`default_rng(SeedSequence([lead_seed, 0x1EAD, seq]))`, one draw, discarded, at trailer
creation in `dispatch` — so the lead is a property of the trailer from the instant it
exists and `dispatched_s + lead_s` is a fixed arrival however many drains later the yard
observes it. Verified: recomputed independently from the documented key (the formula is
the contract, not whatever the method does); strictly positive; median-preserving
(median/target 0.989 over 4000 draws) with sd of the log ratio 0.703 against σ=0.7 — so
`INBOUND_LEAD_MINUTES` means the same thing at every spread, which is the property that
lets the funnel move σ without re-calibrating the median.

**Statelessness, tested at the seam rather than the method.** Two transits dispatching 3
and 9 trailers agree on every shared seq (a shared generator would make #2 depend on #0
and #1 having been drawn); two arms differing in doors and in yard/dock policy see the
identical lead schedule — common random numbers across arms; a different `seed_world`
moves the schedule, so the seed is not decoration.

**The gradient exists.** Eight trailers dispatched at one epoch with σ=0.7, seed 42, land
in yard order `[1, 2, 6, 5, 0, 4, 7, 3]` — not dispatch order — and the yard stays sorted
by `(arrived_s, seq)` with `arrived_s == dispatched_s + lead_s`. At σ=0 the same script
gives `[0..7]`, so the seq tiebreak still holds when every stamp ties.

What that demo does and does not settle (corrected 2026-08-31, caught by the session
resolving 08): it settles that arrival order diverges from dispatch order, which is a
PRECONDITION for the ordering lever having any gradient at all. It is **not** ticket 10's
acceptance criterion (a) — that one is yard CONTENTION, standing trailers regularly
exceeding free doors at drain start, which a single-epoch unit test cannot show. Both of
10's criteria, contention and binding cuts, still need the pilot run and belong to the
funnel (08).

**One refusal the ticket did not list.** The two named contradictions are built, and both
guards sit **above** the `if not ttype` early return rather than after it. With no trailer
type, `standing` must be False (the next branch enforces it), so a spread there is unread
by definition and the pre-existing early return would have discarded it silently — the
exact no-op the doctrine refuses, reached by a different door. Three refusal tests, plus
one proving `spread 0 over median 0` (the default) still passes.

**Deviations from the ticket: none of substance.** Two choices worth recording: the draw
is a *public* `lead_for(seq)` rather than a private helper, because "what lead does trailer
N get" is a real question the yard-metrics surfaces and any future reporting may want to
ask; and the v1 `TrailerTransit` branch in the driver is handed `lead_sigma`/`lead_seed`
too, though `inbound_spec` guarantees they are 0.0 there — so the transit behaves as the
SPEC says rather than as the branch assumes, and the guard is a belt over a working brace
rather than the only thing holding v1's homogeneous leads in place. A driver-seam test
asserts by AST that **both** construction sites pass the whole lead shape: a spec field
nothing forwards reverts to the constructor default in every real run and in no test,
which is the fifth seam's failure mode in miniature.

**Seams 3 and 4 stay deferred**, per ticket 09's precedent: no CLI flag, no run-spec
recording, no `_apply_run_spec`/`_apply_run_shape` restore. Seam 5 needs nothing — the
spec dict already rides the picklable worker payload and the stateless scheme means no
generator ever crosses a process boundary. **Values stay unpicked**: defaults are inert
(σ = 0), and the pilot probe (median ≈ one working day, σ ≈ 0.7) belongs to the funnel (08).

**Owed, not done here.** The derived architecture layer is stale — `context/files.yml` was
updated by hand (the renamed constant, the new one, the new test file) and left in the
working tree unstaged, matching what commits `64b2d31` and `dd44d8a` did; `graph.json` was
already stale at HEAD before this ticket, along with catalog gaps from tickets 09–14, so
the regeneration chain is one job for the architecture-maintainer, not this commit.
`runschema.preflight --check` reports a changed source fingerprint, which it also did with
this ticket's changes stashed — pre-existing, not introduced here.
