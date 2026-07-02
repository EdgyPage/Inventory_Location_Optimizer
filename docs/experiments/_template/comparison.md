# {{ experiment().title }} — comparison

<!-- The run write-up. Fill the Summary/Discussion prose; tables, figures, and formulas
     resolve from experiment.yml. -->

!!! note "Summary"
    <!-- headline takeaway: which strategy won on cumulative task time, by how much vs the
         baseline, and whether it held across inventory variants -->

## Setup

**Pick-time cost model** (`calibrated`; full model + all calibrations on the
[Formula reference](formula-reference.md)):

{{ pick_time_formula((experiment().inventories.keys() | list) | first) }}

{{ pick_calibration_table((experiment().inventories.keys() | list) | first) }}

**Top-3 assignment functions** (full catalogue on the [Formula reference](formula-reference.md)):

{{ assignment_formulas() }}

{% for key, inv in experiment().inventories.items() %}
## {{ inv.label }}

{{ setup_table(key) }}

{{ run_section(key) }}

!!! note "Notes"
    <!-- paste commentary here -->
{% endfor %}

## Discussion

<!-- interpretation and next steps; see Full results for every strategy arm -->
