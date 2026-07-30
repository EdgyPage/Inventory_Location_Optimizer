# {{ experiment().title }}

**The question: how can throughput rise while the amount of work stays the same?**

This experiment answers it by measuring throughput as a **rate** rather than as an end-of-run
number. Plotting cumulative items against elapsed hours makes the rate itself the visible thing —
the slope of the line *is* throughput — so "more work per hour" and "the same work, finished
sooner" become the same picture rather than competing claims.

The run is a picker-**scheduler** A/B: round-robin (`k1_off_rr`) against LPT load-balancing
(`k1_off_lpt`), layout and zoning pinned off, over the full 34-arm assignment suite on both the
store and fulfillment channels. Placement strategies are measured against a
**{{ experiment().baseline }}** baseline; the arms highlighted are
{% for w in experiment().winners %}`{{ w }}`{% if not loop.last %}, {% endif %}{% endfor %}.

**The finding in one line.** LPT lifts throughput by a median **+13.7 % (store)** and
**+5.6 % (fulfillment)** while total labor moves by at most **±0.07 %** across every arm — so the
gain is removed picker *idle* time, not reduced work. The [comparison](comparison.md) page carries
the throughput evidence; [full results](full-results.md) carries the labor side.

## What's inside

- **[Simulation lifecycle](comparison-overview.md)** — how a run works end-to-end.
- **[Formula reference](formula-reference.md)** — pick-time model, task labor, and every
  assignment-function score.
- **Comparison write-up** — headline findings (top-3 vs baseline).
- **[Full results](full-results.md)** — every strategy across the sweep.
- **[Inventory distributions](inventory.md)** — the catalogue this experiment used.
- **[Glossary](glossary.md)** — terms and symbols.

## Inventory variants

{% for key, inv in experiment().inventories.items() %}
- **{{ inv.label }}** — replenishment lead time **{{ inv_lead_time(key) }}**.
{% endfor %}

## Calibrations

{{ pick_calibration_table((experiment().inventories.keys() | list) | first) }}
