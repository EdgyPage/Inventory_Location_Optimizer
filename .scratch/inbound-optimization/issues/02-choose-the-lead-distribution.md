# Choose the lead distribution and its knobs

Type: grilling
Status: resolved

## Question

Per-trailer leads come from a seeded distribution (charter). Decide: the distribution family
(uniform / lognormal / two-point / other) and its knobs, minutes-authored per the settled
denomination (`INBOUND_LEAD_*` naming); where the seeded RNG lives so determinism holds
(seeded `random.Random`/`default_rng`, never the bare global — and remember a knob has five
seams, memory: `config-knob-has-five-seams`, so the spawn-pool propagation path is part of
the answer); when the draw happens (at trailer creation in `TrailerTransit.dispatch`); and
how arrivals integrate — trailers enter the yard ordered by arrival stamp with `seq` as the
tiebreak, replacing the dispatch-order scan in `release()`. Lead 0 (the default) must remain
byte-identical with today.

The batch denomination stays dead (prior decision, "Choose the lead-time denomination" on the
groundwork map); the legacy per-order `lead` argument stays ignored.

## Comments

2026-08-29, from resolving "Define the inbound objective" (10): acceptance criteria for the
chosen distribution — under FIFO ordering it must produce (a) yard contention (standing
trailers regularly exceed free doors at drain start) and (b) binding cuts (drains regularly
ending with unserved standing trailers or partially-unloaded ones); otherwise the
set-composition lever the objective turns on has no gradient and every ordering ties FIFO.
Both are measurable from `YardTransit.stamps` and staged-remainder counts via the
yard-metrics surfaces (07). Rollover stays off. A pilot FIFO run showing neither is a config
to reject before sweeping anything.

## Answer

Resolved 2026-08-29 by grilling (all six recommendations accepted as posed).

**Family: lognormal — ONE family, no selector knob.** Authored as median minutes × a
dimensionless spread: `lead_i = median_s · exp(σ · Z_i)`, `Z_i` standard normal. σ = 0 (the
default) means **no draw at all**: no RNG is constructed and the existing scalar path runs
verbatim — byte-identical by construction, which is how "lead 0 stays byte-identical" is
proven rather than argued. Lognormal is strictly positive (no clamping needed) and its right
tail is what produces overtaken stragglers (the ordering lever's gradient) and trailers that
risk crossing `INBOUND_FEE_THRESHOLD_DAYS` while standing (the fee axis' gradient). A
family-selector knob was declined per the unconsumed-infrastructure doctrine; two-point
("local vs long-haul suppliers") is the recorded runner-up if a future stakeholder story
wants it.

**Knobs — renamed now, while the window is cheap** (CLI flags and run-spec recording are
deferred to the first sweep per ticket 09's precedent, so the rename touches two lines):

- `INBOUND_LEAD_MINUTES` — the median, minutes-authored (renames
  `INBOUND_TRAILER_LEAD_MINUTES`; the CONFIG key is *already* `inbound_lead_minutes`, so
  only the `settings.py` constant and the one mapping line in `sim_config.py` change).
- `INBOUND_LEAD_SPREAD` — σ, dimensionless, default 0.0.

**RNG scheme: seq-keyed stateless.** A trailer's lead is a pure function of
`(seed, domain-tag, trailer.seq)` — e.g. `default_rng(SeedSequence([seed, TAG, seq]))`, one
draw, discard. No generator object exists to pickle, spawn, or restore: determinism holds by
construction, resume-at-clean-boundary needs nothing, and trailer #N draws the same lead in
every arm of a run — common random numbers across arms for free.

**Seed source: derived from `SEED_WORLD` + a fixed domain tag; no new knob.** Leads are
world facts all arms share, like aisle geometry — "same world seed = same warehouse,
catalogue, and lead schedule." A lead-realization sweep, if ever wanted, adds
`INBOUND_LEAD_SEED` then; under the stateless scheme that is a one-line change.

**Draw site (confirmed, not changed):** at trailer creation in `TrailerTransit.dispatch` —
the draw replaces the single `lead_s=self.lead_s` argument. `dispatched_s` semantics are
untouched: the lead runs from creation, and the open trailer's departure at `release()`
does not re-stamp (existing mechanics).

**Arrival integration: already built.** `YardTransit.release()` stamps
`arrived_s = dispatched_s + lead_s` and keeps the yard in `(arrived_s, seq)` order — the
charter's entry order. The ticket's "replacing the dispatch-order scan" is satisfied in the
standing yard; v1 is excluded by the scope guard below.

**Scope guard — loud contradictions in `inbound_spec()`**, the flag's existing pattern:

- `INBOUND_LEAD_SPREAD > 0` without `INBOUND_STANDING_YARD` raises: v1's dock ranks by
  dispatch seq, so a spread would half-work (arrival-batch shifts visible, order scrambling
  invisible) — exactly the silent no-op the config doctrine refuses. v1 code stays untouched.
- `INBOUND_LEAD_SPREAD > 0` with `INBOUND_LEAD_MINUTES == 0` raises: a spread over a zero
  median silently degenerates to constant zero — same doctrine.

**The five seams:** (1) `settings.py` declarations; (2) CONFIG threading via the existing
global map + `inbound_spec()`, which grows `lead_sigma` and `lead_seed` beside `lead_s`;
(3) CLI flag and (4) run-spec recording deferred to the first sweep per the family
precedent (ticket 09); (5) spawn propagation is satisfied by *data* — the spec dict already
rides the picklable worker payload, and the stateless scheme means no RNG state ever
crosses a process boundary.

**Values: machinery only — this ticket picks none.** Defaults stay inert (σ = 0). The
acceptance procedure is the comment above (ticket 10's criteria): a pilot FIFO run must
show yard contention and binding cuts, measured from `YardTransit.stamps` and
staged-remainder counts via the yard-metrics surfaces (07); a config showing neither is
rejected before sweeping. First probe for the pilot: median ≈ one working day, σ ≈ 0.7.
Value selection belongs to the funnel design (08).

**Graduated:** the build is
[Build the lead distribution](15-build-the-lead-distribution.md) (task, unblocked).
