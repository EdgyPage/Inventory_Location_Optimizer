# {{ experiment().title }}

<!-- Overview / definitions for this experiment. Edit the prose; the data wires up from
     experiment.yml, so there are no hard-coded run IDs below. -->

This experiment sweeps the placement strategies against a **{{ experiment().baseline }}**
baseline. The winners highlighted here are
{% for w in experiment().winners %}`{{ w }}`{% if not loop.last %}, {% endif %}{% endfor %}.

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
