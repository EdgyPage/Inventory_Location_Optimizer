# Inventory distributions

The synthetic SKU catalogue this experiment used — the **same catalogue as
[Experiment 7](../experiment-7/inventory.md)**, deliberately: holding the catalogue fixed while
the demand stream changed (the v2 order-draw engine) is what makes Experiment 8 a replication
rather than a new world. Tables and plots are generated from the committed `params.json` /
distribution plots, so they always match the run.

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
