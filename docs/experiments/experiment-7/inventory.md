# Inventory distributions

The synthetic SKU catalogue this experiment used — a **fresh draw**, not Experiment 6's, which is
one of the two reasons its headline numbers differ from earlier sweeps. Tables and plots are
generated from the committed `params.json` / distribution plots, so they always match the run.

*Distribution shorthand in the tables: `tri(a–b)` = triangular between a and b (peak in the
middle), `norm` = bell curve, `mix` = a weighted blend. The two variants below share one
catalogue and differ only in replenishment lead time.*

{% for key, inv in experiment().inventories.items() %}
## {{ inv.label }}

Replenishment lead time: **{{ inv_lead_time(key) }}**.

{{ inv_distribution_table(key) }}
{% endfor %}

## Category shares &amp; demand

<figure markdown>
  ![Category shares](images/{{ experiment().catalogue }}/group_sizes.png){ width=820 }
  <figcaption>SKU count per (handling × category) group.</figcaption>
</figure>

<figure markdown>
  ![Demand across categories](images/{{ experiment().catalogue }}/param_relative_frequency.png){ width=820 }
  <figcaption>Relative pick-frequency distribution per category.</figcaption>
</figure>

<figure markdown>
  ![Equilibrium quantity & reorder point](images/{{ experiment().catalogue }}/equilibrium_qty.png){ width=820 }
  <figcaption>Derived stock targets from each SKU's expected demand.</figcaption>
</figure>
