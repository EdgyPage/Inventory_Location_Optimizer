# The "flows: ALL ZERO" warning read the wrong quantity, and fired on every rung

Type: task
Status: resolved

Found by reading the HEAD ladder's own output (ticket 04) rather than by looking for it.

## The defect, as it printed

Two lines apart, on the same rung of `--config split_staging4`:

    flows (traced, cumulative): refill_passes=10,191 held_retry_touches=50,912
                                held_appends=40,720 queue_admissions=91,779 pool_opens=14,942
    flows: ALL ZERO -- the put-away/receiving path did not execute under cfg=split_staging4.
                       Use --config split_staging4 to exercise it.

Both halves are wrong. The flows are not zero, the put-away path plainly executed 40,720 times,
and the reader is being told to switch to the configuration they are already running.

## Why

The warning was the `else` of the **per-ENTRY-CALL** branch:

    if per_en:
        print('      per ENTRY CALL ...')
        if per_pl:
            print('      per placement: ...')
    else:
        print('      flows: ALL ZERO -- the put-away/receiving path did not execute ...')

`per_en` is non-empty only when the INBOUND evaluator ran (`plan_order` entry calls). So on every
config except the inbound cells it is empty and the warning fires unconditionally, regardless of
what the flows say. A branch about one quantity, carrying a message about another.

The same nesting hid the **per-placement ratios**, which are not an inbound quantity either: they
never printed per-rung on any non-inbound config, while still being computed and fitted -- so the
summary reported ratios the rungs never showed.

## Why it is worse than a wrong line

`Tests/calltree/README.md` instructs the reader that *"`flows: ALL ZERO` in the per-rung output
means **not measured**"*, and records the incident that put it there: a run whose held path
executed thirteen million times reported `held: 0`, and that zero was read as "the path never
ran". **A warning that cries wolf on every rung trains the reader to skip the one line that exists
to stop them trusting a zero.**

## What landed

`_flows_warning(flows, config)` -- a pure helper returning the line or `None`. It reads FLOWS,
which is what it is about; the per-placement print is un-nested; and the `--config
split_staging4` hint is suppressed when that IS the config.

Extracting it is the point, not tidiness: a warning buried in a print block inside a ladder loop
can only be checked by running a ladder, which is why this one survived. The repo's own lesson
from three gate holes in one effort is *"exercise the thing and assert it produced something."*

## Acceptance

Three tests in `Tests/calltree/test_calltree_smoke.py`: silent on the real 40,720-append flow
dict; still fires on `{}` and on all-zero values; and does not advise a config switch to the
config in use. **Proven non-vacuous** -- with `_flows_warning` replaced by the old
always-fires behaviour, two of the three fail with their own messages.
