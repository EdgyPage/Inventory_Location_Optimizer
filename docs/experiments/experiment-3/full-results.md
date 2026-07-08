# Full results

The complete strategy suite — every [assignment function](formula-reference.md) under both
initial layouts — for this experiment, as a **inventory-lead × pick-config** matrix. The
comparison write-up stays focused on the top-3 winners; this is the drill-down.

!!! note "Notes"
    <!-- paste commentary here -->

## The headline

<!-- Note the recurring finding: many strategies — including some "optimizations" — do NOT
     beat the baseline on cumulative task time; the bracket controls lose by design. -->

{% for key, inv in experiment().inventories.items() %}
## {{ inv.label }}

{{ full_suite_section(key) }}
{% endfor %}
