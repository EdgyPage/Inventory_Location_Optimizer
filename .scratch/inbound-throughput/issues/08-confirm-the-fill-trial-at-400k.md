# 08 - confirm the fill trial's answer on the reference catalogue

Type: task
Status: wontfix (closed 2026-09-23; was: open)
Blocked by: 06

Runs only if ticket 06's prototype passes its bar (Q26). The 40k prototype iterates in
minutes; this is the day-scale run that publishes.

## What runs

`inbound_fill` on the reference catalogue (`--profiles-dir` the one-pair reference view,
`phase2-binds-the-reference-catalogue-not-the-default`), the prototype's top three cells plus
the fifo reference (Q18/Q23), the winner pair plus the rider, both stock modes: 16 units.
From a `git archive` snapshot, detached, keep-awake, per `launch-long-drivers-detached` and
`detached-runs-import-the-working-tree`. Sized before launch from ticket 04's priced-unit
wall and the fill's ~2,500 trailers; `--pace-from` the prototype is legal but its weights
come from a different catalogue, so state the choice in the map.

## What it answers

- The fill trial's ranking at scale, on pick labour with the measured floor, both stock
  modes agreeing (the same bar as the prototype).
- Whether the ranking at 400k agrees with the ranking at 40k -- the record's rule that
  contention needs 100k SKUs was about the era's arrival rate; a dispatched declaration
  contends at any scale, and this is the first measurement of whether the ANSWER does too.
- Sim-phase occupancy, the Q7 read that decides ticket 07.

## Publish

Through `publish-experiment` as its own experiment (Q24: it hands nothing to phase 3), with
the fill trial's definition (CONTEXT.md) on the page so a reader does not take it for an era
result; the four-persona review loop as usual.

## Closed 2026-09-23 by the user

The whole inbound-throughput map was closed: the evaluator question is being re-thought from the problem statement, and these tickets will not be relevant by the time it is picked up again.  What was built stays on develop; see the map's closing note.
