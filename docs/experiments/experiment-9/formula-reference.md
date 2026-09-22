# Formula reference

Every calculation in this experiment in one place: the pick-time cost model and task labour
(unchanged from Experiment 8), the placement primitive and the families, and — new with this
experiment — how a gain rule prices a trailer, the placement score the ranking is built on, its
census adjustment, and the measured floor. The [lifecycle page](comparison-overview.md) shows
**when** each of these runs; this page is the canonical **what**. Symbols are in the
[Glossary](glossary.md).

{% set inv0 = (experiment().inventories.keys() | list) | first %}

!!! note "Notes"
    The model is **stated, not fitted**: coefficients are calibrated to plausible pick
    ergonomics, not regressed on a building's time studies; comparisons under the stated model
    are the deliverable. The one new quantity on this page, the placement score, is likewise a
    *stated* price — the same per-pick arithmetic the pickers pay, applied to where the stock
    stands — and it is comparable **only across the arms and cells of one run**, which share a
    demand script by construction. It carries no floor line and is never read across runs.

## Notation

| Symbol | Meaning |
|--------|---------|
| $t_0$ | fixed pick setup time (`pick_intercept`, s) |
| $w,\ V$ | item weight (lb), volume (in³) |
| $h$ | per-pick **handling term** (weight + volume effort) |
| $q$ | quantity picked |
| $y,\ M(y)$ | shelf height; its **height-bracket multiplier** |
| $D_b$ | per-bin **travel** cost (entrance-relative, Manhattan) |
| $f_s,\ q_s$ | SKU $s$'s relative pick frequency, pick quantity |
| $\ell(b)$ | per-bin **labor primitive** (placement proxy) |
| $w_s$ | the planned lines of SKU $s$ — the batches of the run's script that ask for it |
| $\bar c_s$ | the mean at-location pick cost over the bins SKU $s$ currently occupies |
| $S(t)$ | the **placement score** at batch $t$ |
| $U(t)$ | the **census**: planned weight the score could not price at $t$ (no shelf stock) |
| $W$ | the script's planned weight, $\sum_s w_s$ |
| $T$ | trailers standing when a drain plans |

## Pick time

One pick at bin $b$, from [`Warehouse/picking/Pick.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/picking/Pick.py):

{{ pick_time_formula(inv0) }}

{{ pick_calibration_table(inv0) }}

The per-channel constants are as Experiment 8 recorded them and each leaf's committed
`config.json` carries: store $t_0 = 15$ s, $c_{\text{cart}} = 300$ s; fulfillment $t_0 = 10$ s,
$c_{\text{cart}} = 240$ s; fulfillment's height multiplier is 1 by geometry. **The travel
speeds differ by channel too, and the rendered formula above shows the store's only**: the
store walks $v_x = 3$, $v_y = 2$ ft·s⁻¹ (cross-aisle, along-aisle), so
$D_b = x_{\text{phys}}/36 + y_{\text{phys}}/24$ s; fulfillment walks $v_x = 2$, $v_y = 4$, so
$D_b = x_{\text{phys}}/24 + y_{\text{phys}}/48$ s — faster along the aisle than across it,
the reverse of the store, which is a cart-and-tote difference and not a typo. Both pairs are
in the leaf configs under `images/`; the factor register on the lifecycle page does not list
the speeds among its varied factors, a gap in its writer recorded as a follow-up. What
changed is the **wave fraction**: the era derives it from the declared demand (store
0.245 %, fulfillment 1.81 % of the SKU pool per wave) instead of authoring 15 % / 20 %.

## Task labor — handling + travel + cart { #task-labor }

$$W_{\text{task}} \;=\; \underbrace{\sum_{\text{stops}} M(y)\,(t_0 + q\,h)}_{H}
\;+\; \underbrace{x_{\text{trav}}\,x_{\text{pace}} + y_{\text{trav}}\,y_{\text{pace}}}_{T}
\;+\; \underbrace{c_{\text{cart}}\,\max(0,\ \text{carts}-1)}_{C}$$

Unchanged from [Experiment 8](../experiment-8/formula-reference.md#task-labor). Two labours are
new beside it and recorded per day: **put-away** (a put crew's walk to the chosen slot plus
per-item handling) and **unloading** (the receiving crew's per-pack handling at the door). Both
are flows: over forty days every trailer is unloaded and every unit put away whatever order the
dock works in, so their totals are invariant to the dock rule — which is why the ranking cannot
be built on them, below.

## Placement primitive $\ell(b)$ { #placement-primitive-ellb }

$$\ell(b) \;=\; M(y_b)\,(t_0 + h) + D_b,\qquad
D_b = x_{\text{pace}}\,x_{\text{phys}} + y_{\text{pace}}\,y_{\text{phys}}$$

The families rank free bins by $\ell(b)$ and demand; the two this run swept —
`rank_cartlabor` (store) and `rank_minlabor` (fulfillment) — place each arriving unit where it
least burdens the busiest aisle, scored with and without cart-swap effort. Their objectives, and
every other family's, are generated below from the run's own rule catalogue.

## How a gain rule prices a trailer { #how-a-gain-rule-prices-a-trailer }

The dock's **gain** rules do not carry a score of their own. They re-run the site's placement
rule **virtually** on each standing trailer's contents, against a frozen view of the empty slots
at the drain's start, and compare two placements per trailer:

$$\text{gain}(\tau) \;=\; \underbrace{\text{cost}\bigl(\tau \mid \text{the slots the OTHER trailers would leave}\bigr)}_{\text{if deferred}}
\;-\; \underbrace{\text{cost}\bigl(\tau \mid \text{today's empty slots}\bigr)}_{\text{if unloaded now}}$$

where $\text{cost}$ is the placement rule's own pick-side price of the bins it would choose
($\ell(b)$ over the load, plus the put-away walk). The trailer with the largest gain — the one
that loses most by waiting — is unloaded first; its slots are removed from the view, and the
greedy repeats over the trailers left, so a plan over $T$ standing trailers costs $T(T+1)$
virtual placements. `gmyopic` prices against today's empty slots only; `gforecast` also counts the
slots the day's released picks are about to free; `ggated_h` puts any trailer past $h \times$ the
free threshold at the front in arrival order before the greedy runs; `fsight` lets the forecast
read the orders of the next 5 days or the whole run; `gmyopic_k8` runs the greedy over the eight
longest-standing trailers only. Under the FIFO restock rider every virtual placement lands in a
uniform-random bin, so every trailer's gain is the same and the rule degenerates to arrival
order — the mechanism behind the rider's role as a control.

## The placement score { #the-placement-score }

Why not rank on labour: over forty days every trailer is unloaded and every pick is made, so
**every flow total is invariant to the order** — the first ranking on total site labour came back
an exact tie. An ordering rule changes *when* stock reaches a shelf and *which* shelf was free,
which is a property of the **state**. The score is a state read, taken every day:

$$S(t) \;=\; \sum_{s\ \text{stocked}} w_s\,\bar c_s(t),\qquad
U(t) \;=\; \sum_{s\ \text{unstocked}} w_s$$

what the run's own planned lines would cost served from where the stock stands, with each SKU
weighted by the batches of the script that ask for it and priced at the **mean by bin count**
over the bins it occupies. The ranking uses the steady-state mean of $S$, summed over a unit's
two channels and over the pair's two starting layouts. **This is the arithmetic behind the
finding:** one arriving pack joins a SKU's $N$ existing bins and moves its term by
$w_s\,(c_{\text{new}} - \bar c_s)/(N+1)$ — and on this script $w_s$ is about one line for most
touched SKUs.

**The census, and the adjustment.** A planned line whose SKU has nothing on any shelf costs
$S$ nothing, so a rule that leaves more in the yard would read cheaper for it. The ranking
therefore scores the **census-adjusted** value, per batch:

$$S^{\ast}(t) \;=\; S(t)\,\frac{W}{W - U(t)}$$

— each unpriced line charged the leaf's own mean priced line, nothing invented, and exactly
$S(t)$ when $U(t) = 0$. $W$ is rebuilt from the run's batch script by the same function the
simulator weighted with. A census that is *material* (above 1 % of the score) still refuses the
ranking outright: no pricing rule makes a run decided by availability into one decided by
placement. On this run the census is under 0.004 % of the score in every cell; the adjustment moved
the `fifo`-to-`gforecast` gap on the winner pair from 0.143 % on the raw score to 0.117 %
on the adjusted one (the ratio of the `owed_unadjusted` fields, and the paired per-batch
`gap_pct` the ranking table prints; the ratio of the adjusted means reads 0.116 %, the
difference being mean-of-ratios against ratio-of-means).

## The floor, measured { #the-floor }

Two cells closer than the floor are a **tie**, and the yard's overage orders them. The floor is
measured per rule pair, not declared: every cell draws the same script from the same seed, so the
per-batch difference between a cell and the reference is **paired**, and its serial correlation
is the simulator's. For each cell,

$$d_t = \frac{S^{\ast}_{\text{cell}}(t) - S^{\ast}_{\text{ref}}(t)}{S^{\ast}_{\text{ref}}(t)}$$

is bootstrapped by the same moving-block method as every interval on this site (2,000
resamples, block length $\approx n^{1/3}$, seed 0), and the floor is the **widest 95 % half-width
among the cells** — so two cells closer than any cell's own uncertainty tie. On this run: 0.080 %
on the winner pair, 0.017 % on the rider, against the 0.1 % that was declared before the floor
was measured. The declared value remains the fallback for a run whose per-batch series cannot be
read, and the record says which applied.

## Overage { #overage }

$$\text{overage} \;=\; \sum_{\text{trailers}} \max\bigl(0,\ \text{detention days} - 0.40\bigr)$$

with detention counted from arrival to emptied, in calendar days of the site's clock, and
0.40 the threshold every cell of this run declares (recorded per cell in the ranking
artifact's `tie_break.threshold_days`). A count, never converted to money; a carrier's rate
does that. It is a **censored** sum: a trailer under the threshold contributes nothing, so
the total does not scale with the threshold and must be re-cut from the per-trailer
distribution at any other free period.

The crews the era derives — pickers, receivers, putters — come from the closed-form
staffing derivation in `Optimization/simconfig/staffing.py` (with the coverage and
first-time-completion inputs from `Optimization/simconfig/coverage.py`), which sizes each
crew from the declared demand and a declared confidence rather than by simulation; the
outputs this run used are the `run.site_crews` block of the ranking artifact and each
leaf's `num_pickers`. The 1 % census materiality threshold is likewise declared, not
derived; a run above it is refused with the record written and no cells named.

## The families

The full catalogue, generated from the run's own `rule_catalog.json` — every family's objective
and the symbol that implements it, with the two this run swept marked as such:

{{ rule_catalog() }}

## How the comparison statistics are computed { #comparison-statistics }

Every arm-versus-baseline number on the per-leaf figures comes from one per-leaf table,
`vs_baseline.csv`, staged beside each experiment's figures — the same columns, bootstrap and
Holm correction [Experiment 8's formula page](../experiment-8/formula-reference.md#comparison-statistics)
documents. The ranking's own statistics are the floor above and the paired gap with its interval
on every row of the ranking table.
