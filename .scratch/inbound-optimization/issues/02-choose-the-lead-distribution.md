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
