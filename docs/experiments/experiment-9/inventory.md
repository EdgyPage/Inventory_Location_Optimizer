# Inventory distributions

The synthetic SKU catalogue this experiment used — the same 400,000-SKU catalogue as
[Experiment 8](../experiment-8/inventory.md), on its immediate-replenishment pair only, because
the campaign's staffing was derived and pinned for that one pair and a run refuses a pair the pin
does not name. Tables and plots are generated from the committed `params.json` / distribution
plots, so they always match the run.

What differs from Experiment 8 is not the catalogue but the **stock declaration** over it: under
this era both channels sit on a line floor (about one of the SKU's own mean order lines on the
shelf, every pick reordering what it took), so the order-up-to quantities and reorder points are
the era's, not the catalogue's — the [lifecycle page](comparison-overview.md#the-inventory-model)
carries the declared distribution.

*Distribution shorthand in the table: `tri(a–b)` = triangular between a and b, `norm` = bell
curve, `mix` = a weighted blend.*

{% for key, inv in experiment().inventories.items() %}
## {{ inv.label }}

Replenishment lead time: **{{ inv_lead_time(key) }}** between reorder and dispatch; the
trailer's transit is a further lognormal delay of median 480 minutes.

{{ inv_distribution_table(key) }}
{% endfor %}

## Category shares & demand

<figure markdown>
  ![Category shares](images/{{ experiment().catalogue }}/group_sizes.png){ width=820 }
  <figcaption>SKU count per (handling × category) group.</figcaption>
</figure>

<figure markdown>
  ![Demand across categories](images/{{ experiment().catalogue }}/param_relative_frequency.png){ width=820 }
  <figcaption>Relative pick-frequency distribution per category. Demand is thin against the
  catalogue: the forty-day script asks for 24,725 store lines and 119,223 fulfillment lines
  across 400,000 SKUs, which is the fact behind this experiment's finding.</figcaption>
</figure>
