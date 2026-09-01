# Define the calibrated era

Type: grilling
Status: open

## Question

Decide the MECHANISM of the calibrated era, and which levers calibration may legally move.
This is the map's root decision: every other ticket consumes its outputs.

**(a) Is the era `SHIFT_DRAIN_OR_CAP` + `releases_per_day`, as-built?** The dormant
machinery already implements the charter's day model: `SHIFT_DRAIN_OR_CAP=True`
(`Optimization/config/settings.py:197`, off by default) forces `cut_at_day_end` on (a cap
without carry loses demand), gives the receiving crew the SITE's day — one boundary for
every crew, overriding the dock's own day knobs — and closes each day with the
drain-or-cap ledger (`timeline.shift_end`: the drain instant when the day drained, else
the cap; days stay origin-aligned). `releases_per_day=N` (`Warehouse/kernel/timeline.py:231`)
cuts the day into N fixed slots — batch i releases at `max(ready, origin + i·cadence)`,
`day_of(i) = i // N`, and `released_late` records every second a batch lags its slot.
Under pacing, the dock's day-remainder-per-batch grant becomes CORRECT, because a batch no
longer outlives its day — the clock mismatch the pilot found dissolves without a mechanics
rewrite. The alternative is a soft equilibrium (no whistles, staffing sized so makespans
happen to fit): weaker, no era break, and "the day" stays a fiction the moment any queue
drifts.

**(b) The declared shift length.** `work_day_seconds` defaults to `shift_seconds()` =
28,800 s. Is one 8-hour production stretch the declared S, or something else?

**(c) Which levers may calibration move to make a batch fit its slot?** A store batch is
~19 working days of pick makespan at 25 pickers (pilot measurement). Three levers:

- **Picker count** — pure staffing, but bounded below by the heaviest single aisle: one
  task per aisle is atomic (`Workload_Builder.py` Task.from_batch), so parallelism has a
  structural floor the calibration run must MEASURE before any count is chosen.
- **Cadence** (`releases_per_day`) — integer slots per day; one batch per day is a
  legitimate equilibrium if makespans fit.
- **Batch content** (`STORE_BATCH_MEAN` = 0.15 of the channel's SKU count per batch,
  `FF_BATCH_MEAN` = 0.20) — moving it rewrites the batch script (the fingerprint keys on
  it), which the calibrated era does anyway; the cost is honesty in the record, not
  comparability.

**(d) Name what the era makes inert and what it breaks**, so nothing is discovered later:
`recv_day_seconds` / `recv_day_origin` apply flag-off only (the dock inherits the site day
under drain_or_cap); archive row-comparability ends (a declared results era, like naming a
trailer type); and if (c) touches batch content, the script itself is new.

Facts with anchors: [assets/calibration-facts.md](../assets/calibration-facts.md),
sections *The working-day machinery* and *Batch content and arm-invariance*.

**Recommendation:** (a) yes — drain-or-cap paced days ARE the era; the close-out ledger
gives a per-day drained-vs-capped verdict for free. (b) S = 28,800 s, the site's existing
shift. (c) all three levers legal, moved in order: pickers first (the thing this map
exists to declare — and the seam must be built regardless), cadence second, batch content
last and only if the heaviest-aisle floor proves the first two cannot compose. (d) record
all three consequences in the resolution verbatim.
