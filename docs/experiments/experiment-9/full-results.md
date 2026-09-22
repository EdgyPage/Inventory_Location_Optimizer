# Every cell, every arm

*This is the evidence page for the labour half of the [Results](index.md) summary, and it carries
every cell in the sweep, both rankings, and the per-arm figures.*

!!! warning "Two kinds of hours on this page"
    **Labour hours** is total task time — the hours of hands-on picking, added up across the
    crew, as if one picker had done it all alone. It answers *how much work there was*; it is
    not a staffing estimate. **Elapsed hours** is how long the crew took to clear the run —
    the number closest to "when does the shift end". Both are in the table below, and neither
    moves between cells by more than a few hours in seven thousand: **the shift does not get
    longer or shorter under any dock rule.**

## The ranking, and what decided it

On the placement pair every cell shares — Experiment 8's winners, `rank_cartlabor` in store and
`rank_minlabor` in fulfillment:

{{ unload_ranking() }}

Three facts the table states that a rank list alone could not:

- **The score separated the cells, barely.** `discriminating` is true: the five deferring rules
  sit in one tie group 0.04–0.12 % cheaper than `fifo`, outside the measured floor of 0.080 %,
  and the other five sit with `fifo` in a second. Two groups, not ten ranks.
- **Inside a group the yard decides.** Every `decided_by` reads `overage`. The first three
  cells are the three smallest yard bills among the deferring rules.
- **The score is census-adjusted** — in plain words, **a rule cannot score better by leaving
  product in a trailer instead of on a shelf; the ranking checks for that.** A planned line
  whose product has nothing on any shelf costs the raw score nothing, and the deferring rules
  strand a few hundred more such lines per day than `fifo` does. The ranking prices those lines
  at the leaf's own mean priced line before ranking. The adjustment is stated on the
  [formula reference](formula-reference.md#the-placement-score).

<small>`discriminating`, `noise_floor` and the `adjusted` flag on every unit are in
[`data/unload_ranking.json`](data/unload_ranking.json).</small>

**How much leverage the score could have had.** The ranking also records, per cell and
channel, how much of what the dock put away was picked again inside the window — the only
stock an unloading rule has any say over. On the winner pair in the reference cell:

| channel | units put away by the dock | of which picked again inside the window | share of all picks that came from a dock-filled slot |
|---|---:|---:|---:|
| store | 479,931 | 9.3 % | 8.7 % |
| fulfillment | 2,408,050 | 23.8 % | 22.8 % |

<small>`inbound_repick` in [`data/unload_ranking.json`](data/unload_ranking.json), unit grain,
both starting layouts summed; the deferring cells read within a percentage point of these.
Nine tenths of the store's put-away and three quarters of fulfillment's were not asked for
again in forty days, which is the arithmetic behind a 0.12 % spread.</small>

### The rider as a control { #the-rider-as-a-control }

Every cell also runs the FIFO restock pair — first-free-slot put-away in both channels. Until
this experiment the ranking read it as an independent replication and let its disagreement veto
the winner pair's order. It is a **control**: under FIFO put-away a gain rule has no slot to
defer for, so it degenerates to arrival order, and the rider's ranking says only whether a rule
does anything at all when the slotting beneath it is random.

{{ unload_ranking(control=True) }}

The verdict the tool records: **inert** under the rider — byte-identical to the reference on
score and overage — `ggated_h100`, `gmyopic`, `gmyopic_k8`; **moved** — `lifo`, `gforecast`,
the three `ggated` rules below `h100`, and both `fsight` rules, because their rule reorders on
something other than the slotting beneath it. <small>`rider_control` in
[`data/unload_ranking.json`](data/unload_ranking.json).</small>

### A second model disagrees with the score, which is the finding restated

There is no placement signal here for two models to agree on, and the second one says so.
Each placement is also priced by an independent closed-form model (the expected pick over the
same slots at keyframe cadence), and at these floors it orders the cells differently from the
score on both pairs. On this site disagreement is the informative direction: it says the two
models share no signal at gaps this size, which does not undercut the ranking — it confirms
that the ranking is the tie-break's. <small>`exact_check` in
[`data/unload_ranking.json`](data/unload_ranking.json).</small>

## The labour bound, per cell

Hands-on labour over the forty days barely moves between cells. On the winner pair's uniform-
start arms:

| cell | store labour, h | fulfillment labour, h | store elapsed, h | fulfillment elapsed, h |
|---|---:|---:|---:|---:|
| `k1_off_fifo` | 7,397 | 6,599 | 243.2 | 288.8 |
| `k1_off_lifo` | 7,400 | 6,599 | 243.2 | 288.9 |
| `k1_off_gmyopic` | 7,398 | 6,599 | 243.2 | 288.7 |
| `k1_off_gforecast` | 7,399 | 6,595 | 243.7 | 288.6 |
| `k1_off_ggated_h025` | 7,403 | 6,596 | 242.9 | 288.7 |
| `k1_off_ggated_h050` | 7,401 | 6,596 | 242.8 | 288.7 |
| `k1_off_ggated_h100` | 7,397 | 6,599 | 243.2 | 288.8 |
| `k1_off_fsight_w5` | 7,400 | 6,597 | 243.3 | 288.8 |
| `k1_off_fsight_wall` | 7,401 | 6,597 | 243.2 | 288.8 |
| `k1_off_gmyopic_k8` | 7,400 | 6,598 | 243.0 | 288.8 |
| `k1_off_inb_off` | 7,408 | 6,598 | 243.2 | 288.7 |

<small>`labor_hours` and `elapsed_hours` for `uni_rank_cartlabor_norsl` (store) and
`uni_rank_minlabor_norsl` (fulfillment), one row per cell, from
[`data/whatif_volume.json`](data/whatif_volume.json). The spread across the ten yard cells is
6 hours in 7,400 (store) and 4 in 6,600 (fulfillment).</small>

The census over every same-arm comparison against the reference cell, generated from the run's
own comparison record rather than transcribed. **Read the last column this way:** the sign
test asks whether the cells moved labour in one direction more often than a coin flip would; a
value like `1.0e-02` means "one chance in a hundred that a coin would do this", i.e. a real
lean, and `1.0e+00` means "a coin would do this every time", i.e. no lean.

{{ census_table('labor_delta_vs_ref_pct', places=3) }}

Where the store row reads as a real lean, look at the size beside it: the median change is a
few thousandths of a percent — a direction the simulator can detect because it replays the
identical day, and a size no floor could. **Statistically real and operationally nothing are
both true here.**

And the throughput side, for completeness — the dock rule does not touch the pick crew's
schedule, so the direction should be a coin flip, and where it is not the size column says
how little it leaned:

{{ census_table('thr_gain_vs_ref_pct', places=3) }}

## Every cell, every arm

Arm names read `<starting layout>_<placement rule>`: `uni_…` starts from a random layout,
`opt_…` from the rule's own ideal layout ([glossary](glossary.md#initial-layout)). Each cell runs
six arms per channel: the winner pair and the rider, from both starts. The curated per-arm
figures below are the reference cell's; every other cell's are staged under `images/` by cell
name, and the per-leaf `vs_baseline.csv` and `per_run_summary.csv` tables under `data/` carry
each arm's numbers.

{% for key, inv in experiment().inventories.items() %}
### {{ inv.label }} — cell `{{ experiment().run }}`

{{ full_suite_section(key) }}
{% endfor %}

### The site yard, cell `{{ experiment().run }}`

{{ site_suite_section((experiment().inventories.keys() | list) | first) }}
