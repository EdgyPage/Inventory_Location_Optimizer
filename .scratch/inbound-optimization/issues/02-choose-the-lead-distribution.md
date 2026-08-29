# Choose the lead distribution and its knobs

Type: grilling
Status: open

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
