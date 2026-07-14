# Inventory distributions

The synthetic SKU catalogue this experiment used — the **same seed-42, 150,000-SKU bell catalogue as
[Experiment 3](../experiment-3/inventory.md)**, frozen once and replayed across all 9 layout cells so
the inventory never varies. Tables and plots are generated from the committed `params.json` /
distribution plots, so they always match the run.

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
  ![Relative pick-frequency by category](images/{{ experiment().catalogue }}/param_relative_frequency.png){ width=820 }
  <figcaption>Bell-shaped relative pick-frequency: per-category normals (food fast ~0.80,
  furniture slow ~0.05) for store SKUs; an equal-weight mixture of three normals for fulfillment.</figcaption>
</figure>

<figure markdown>
  ![Demand mass across categories](images/{{ experiment().catalogue }}/demand.png){ width=820 }
  <figcaption>Realised demand mass (frequency × quantity) per category — the skew velocity zoning
  tries (and here fails) to exploit.</figcaption>
</figure>

## Stock targets

<figure markdown>
  ![Equilibrium quantity & reorder point](images/{{ experiment().catalogue }}/equilibrium_qty.png){ width=820 }
  <figcaption>Derived stock targets from each SKU's expected demand.</figcaption>
</figure>

<figure markdown>
  ![Pick quantity distribution](images/{{ experiment().catalogue }}/param_quantity.png){ width=820 }
  <figcaption>Units-per-pick distribution per category.</figcaption>
</figure>

## Item physical distributions

<figure markdown>
  ![Weight distribution](images/{{ experiment().catalogue }}/weight.png){ width=820 }
  <figcaption>Item weight per category (drives the handling term and the store weight penalty).</figcaption>
</figure>

<figure markdown>
  ![Volume vs weight](images/{{ experiment().catalogue }}/volume_vs_weight.png){ width=820 }
  <figcaption>Volume–weight relationship across the catalogue.</figcaption>
</figure>

<figure markdown>
  ![Dimensions](images/{{ experiment().catalogue }}/dimensions.png){ width=820 }
  <figcaption>Item bounding-box dimensions, which set the storage-size bucket.</figcaption>
</figure>
