# Sequence the inbound funnel

Type: grilling
Status: open

## Question

Does the inbound funnel's PHASE 1 also wait for the calibrated era, or run now under the
historical continuous regime? (Phase 2 is already held — settled at the pilot resolution.)

The original recommendation ("run phase 1 now — calibration cannot invalidate its
ranking") was WRONG, and the correction is on record: the calibrated era turns on
`cut_at_day_end`, which "changes WHICH units get picked in WHICH batch"
(`strategy_runner.py:549`), and if the era's calibration touches batch sizing the script
itself changes. A phase-1 ranking taken now would feed a phase 2 running in a different
era — and the `inb_off` anchor cell would then compare ACROSS eras, breaking the very
transfer check it exists to perform (funnel design, inbound map ticket 08).

The trade: phase 1 is ~3 h of simulation and unblocks the inbound map's one remaining
ticket ([Extend the gain bundles](../../inbound-optimization/issues/20-extend-the-gain-bundles.md));
holding it costs that wait, running it now risks running it twice and produces a ranking
whose regime nobody will ever run again.

On resolution, edit the inbound map's campaign entry (Not-yet-specified, "The funnel
campaign") to record the decision — that map's execution order is where the answer lives;
this ticket is where it gets decided.

**Recommendation:** hold phase 1. Cheap to defer, expensive to duplicate, and a number
with no consumer is not worth 3 hours. The funnel restarts from phase 1 under the
calibrated era once this map lands.
