# Fit the store's window to its own steady state

Type: grilling
Status: open

Graduated 2026-09-08 from [Re-read the check under the fielded floor](25-re-read-the-check.md),
failure 2. HITL. Skills: `grilling` + `domain-modeling`.

## Question

The store section does not reach a steady state inside the 40-day reference window, so no clause
that judges a LEVEL can be read on it. Decide what the window should be -- or whether the window is
the wrong lever entirely.

**What 25 measured** on `comparison_20260908_075736` (fifo, 40 era days, the reference pair):

- days drained **5/40, and 0/20 inside the measured window 20-39** -- every one of the last twenty
  days capped;
- picking realized **0.905 across days 0-39 (IN band of 0.850) but 0.963 across days 20-39 (OUT,
  +0.113)**; the second half works 693,672 s/day against the first half's 609,703, **+14%**;
- yet seconds per unit picked is **FLAT**: 100.41 over days 0-4 against 99.19 over days 35-39. The
  store is not getting more expensive per unit, it is doing more units. Fulfillment is flat too
  (18.52 -> 18.72), and ADR-0003's own-bin share of 0.000 rules out fragmentation from the other
  side;
- `missed_share` **rises for about thirty days and then falls**: 0.109 -> 0.229 across days 0-39
  (+0.120), but 0.288 -> 0.170 within days 20-39 (-0.118). The measured window is sitting inside a
  hump.

**Why this may be structural rather than a warm-up.** The store's implied coverage puts **every**
store SKU at Q = 1 on the line floor, running base stock (memory
`coverage-in-days-floors-the-store-section`): each pick empties a bin and each replenishment opens a
fresh one -- 77,544 put events for 249,640 units over the run. A shelf shaped like that may have a
settling time set by the replenishment cycle rather than by anything a window can outlast, in which
case a longer window is expensive and still wrong.

**The open questions**, in the order they gate each other:

1. Is the hump a transient with an end? Days 0-39 alone cannot say. The cheapest evidence is one
   longer run (60 days? 80?) on the store leaf only -- but a longer window multiplies every later
   campaign's cost, so the length is a decision, not a default.
2. If it settles, does the reference window move for BOTH channels or only the store? Fulfillment
   already passes its `missed_share` trend clause at 20-39 (level 0.118, trend -0.010), so a longer
   window buys fulfillment nothing and costs it wall time. The era currently declares ONE window for
   the site ([Sequence the inbound funnel](05-sequence-the-inbound-funnel.md), decision 4), and the
   inbound map's
   [Re-size the funnel in site days](../../inbound-optimization/issues/24-resize-the-funnel-in-site-days.md)
   takes its depth from it -- so a per-channel window is a change to that contract, not a parameter.
3. If it does NOT settle, the question is not the window at all: it is whether a Q = 1 store shelf
   can be in equilibrium under the era, which reaches back to
   [Choose the coverage floor](15-choose-the-coverage-floor.md) and the floor's own premise.
4. Whatever the answer, `missed_share`'s LEVEL clause (store 0.229, fulfillment 0.118, both against
   0.078 expected) cannot be judged until this resolves -- it is currently being read from inside
   the transient. Say explicitly whether the level is expected to land at 0.078 once settled, or
   whether the stamped fill rate is itself the thing to re-examine.

Do NOT re-run the 40-day check to answer this: 25 already took it and its readings are above.
Whatever run this ticket decides on is a new shape, and its cost is part of the decision.

## Done when

The reference window is declared (length, per-channel or not, and the reasoning), OR the effort is
redirected at the store's shelf shape with this ticket recording why the window was the wrong lever.
Either way the answer names what happens to `missed_share`'s level clause.
