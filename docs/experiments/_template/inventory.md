# Inventory distributions

The synthetic SKU catalogue this experiment used. Tables and plots are generated from the
committed `params.json` / distribution plots, so they always match the run.

A catalogue describes the goods — size, weight, handling, demand, lead time — and **not how much
of each is held**: stock levels are declared by the run at setup, in days of cover, and are
covered on the lifecycle page under
[the stock declaration](comparison-overview.md#stock-declaration). The levels the run ended up
fielding are in the setup table on that same page.

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
