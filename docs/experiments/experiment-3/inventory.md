# Inventory distributions

The synthetic SKU catalogue this experiment used. Tables and plots are generated from the
committed `params.json` / distribution plots, so they always match the run.

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
  <figcaption>Per-category relative pick-frequency distributions. Unlike Experiments 1–2 (uniform
  frequency), each store category is drawn from its own <strong>normal</strong> (food ~0.80,
  chemical ~0.50, clothing/electronic ~0.35, seasonal ~0.15, furniture ~0.05) and fulfillment SKUs
  from an equal-weight mixture of three normals (means 0.15 / 0.25 / 0.55).</figcaption>
</figure>

<figure markdown>
  ![Equilibrium quantity & reorder point](images/{{ experiment().catalogue }}/equilibrium_qty.png){ width=820 }
  <figcaption>Derived stock targets from each SKU's expected demand.</figcaption>
</figure>

<figure markdown>
  ![Weight distribution](images/{{ experiment().catalogue }}/weight.png){ width=820 }
  <figcaption>Per-category weight distributions.</figcaption>
</figure>

<figure markdown>
  ![Carton dimensions](images/{{ experiment().catalogue }}/dimensions.png){ width=820 }
  <figcaption>Length / width / height distributions across the catalogue.</figcaption>
</figure>
