# Full results

The complete strategy suite — every [assignment function](formula-reference.md) under both
initial layouts — for this experiment, as a **inventory-lead × pick-config** matrix. The
comparison write-up stays focused on the top-3 winners; this is the drill-down.

!!! note "Notes"
    Worth checking here specifically: whether the bell demand shape **reordered** the suite or
    merely rescaled it. The comparison page reports that the store win sharpens and the
    fulfillment win shrinks, but a headline cannot distinguish "the same families, different
    margins" from "different families won".

    The matrix below can. If the losing tail and the bracket controls hold the same relative
    positions as in [Experiment 2](../experiment-2/everything-else.md), the demand shape is
    scaling an existing effect rather than creating a new one — which is the weaker and more
    likely reading.

## The headline

<!-- Note the recurring finding: many strategies — including some "optimizations" — do NOT
     beat the baseline on cumulative task time; the bracket controls lose by design. -->

{% for key, inv in experiment().inventories.items() %}
## {{ inv.label }}

{{ full_suite_section(key) }}
{% endfor %}
