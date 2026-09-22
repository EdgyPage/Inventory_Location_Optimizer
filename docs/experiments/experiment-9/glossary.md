# Glossary

Definitions for every term the [lifecycle](comparison-overview.md) and result write-ups cite.
Each entry has a stable anchor — link to one with `glossary.md#<id>`. The pick-side terms are
Experiment 8's, unchanged; the site and yard terms are new with this experiment.

## The experiment's own vocabulary

### Cell { #cell }
One frozen variant of everything *outside* the placement loop. This sweep has eleven cells that
differ only in the **dock rule** — ten rules over a standing yard plus the no-yard pole
`k1_off_inb_off`. The same six arms per channel run in each cell.

### Dock rule — unloading policy { #dock-rule }
The rule the dock uses, when a door comes free, to decide which standing trailer to unload next.
The one thing the cells differ on. `fifo` (first come, first served) is the reference.
*Avoid:* unload sequence — the rule emits a priority over the standing loads, and the crew's day
cuts it into what was actually served.

### Arm { #arm }
One complete simulated channel run: an [initial layout](#initial-layout) (`uni` or `opt`) paired
with one placement rule. Six per channel per cell here — the winner pair and the rider, from both
starts.

### Unit — arm pair { #unit }
The coupled run of one store arm and one fulfillment arm over one shared site yard. A cell's
score for a placement pair sums its two units (both starting layouts).

### Rider { #rider }
The FIFO restock pair (`fifo` / `fifo`) every cell carries beside the winner pair. A **control**,
not a replication: under first-free-slot put-away a gain rule has no slot to defer for and
degenerates to arrival order, so the rider says only whether a dock rule does anything at all,
and its ranking never vetoes the winner pair's.

### Coupled { #coupled }
Store and fulfillment served by one site: one dock, one yard, one receiving crew, one pool of
putters. The pick side stays separate — each channel picks its own disjoint racking with its own
crew and order stream.

### Modeled hours { #modeled-hours }
Every "hour" on this site is the cost model's own output. It compares effort between arms; it is
**not** wall-clock time or a staffing estimate. Percentages between two arms are the meaningful
quantity.

### Site day { #site-day }
One working day of the site's clock: one wave released per channel, one drain at the dock. The
run is forty of them.

## The yard

### Trailer { #trailer }
One dispatched load: the packs of the reorders that triggered it, in the packing the warehouse
was sized from. Dispatched when stock falls to a reorder point; arrives after its transit lead.

### Lead — transit { #lead }
A trailer's delay from dispatch to arrival, drawn per trailer from a lognormal with median 480
minutes and sigma 0.7 — a world fact every arm shares, so trailer $N$ arrives at the same time in
every cell.

### Yard { #yard }
Where arrived trailers wait for a door. **Yard depth** is how many stand at the start of a
drain — what an ordering rule chooses among; a yard of one has no order to choose.

### Door { #door }
One of the dock's four unloading positions. A trailer at a door is **staged**; the receiving crew
empties it there.

### Drain { #drain }
One working day's dock activity: while a door is free and trailers stand, the cell's rule picks
the next one and the crew empties it. **Contended** when it starts with trailers standing and no
free door; **binding-cut** when the crew's day ends with trailers still staged.

### Detention { #detention }
The span from a trailer's arrival to its being emptied, in calendar days.

### Free threshold { #free-threshold }
The detention a carrier does not bill — 0.40 days in every cell here, set per cell by the
phase-2 axis and recorded per cell in the ranking artifact (the factor register's 2.0 days is
the run-level default the cells override).

### Overage { #overage }
The days a trailer's detention runs past the free threshold, summed over trailers: the physical
basis of the detention fee, reported as a count and never converted to money or mixed into
labour hours. The ranking's tie-break.

### Receiving crew { #receiving-crew }
The crew that unloads at the doors, shared by both channels and sized by the simulator from the
declared demand. Its day can end before the yard is empty (a binding cut).

### Gain { #gain }
What a standing trailer loses by waiting: the pick-side price of the slots it would get today,
against the slots the other trailers would leave it. The gain rules unload the largest gain
first. Policy-relative and never persisted; the [formula reference](formula-reference.md#how-a-gain-rule-prices-a-trailer)
has the arithmetic.

### Futuresight { #futuresight }
A gain rule allowed to read orders not yet released — the next five days (`fsight_w5`) or the
whole run (`fsight_wall`). An oracle bound on what foresight could buy, not a policy a site could
run.

## The ranking

### Placement score { #placement-score }
What the run's planned lines would cost the pickers served from where the stock currently
stands: each SKU weighted by the batches of the script that ask for it, priced at the mean over
the bins it occupies. A state read, taken every day; the ranking uses its steady-state mean.
Comparable only within one run.

### Census { #census }
Planned lines whose SKU has nothing on any shelf — demand the score cannot price. Reported
beside the score; priced into the ranking's adjusted score at the leaf's mean priced line; and
if material (above 1 % of the score) it refuses the ranking outright.

### Floor — noise floor { #floor }
The gap below which two cells tie on the score. Measured per rule pair as the widest 95 %
moving-block bootstrap half-width of any cell's paired per-batch gap to the reference; 0.080 % on
the winner pair here. The declared 0.1 % is the fallback when the per-batch series cannot be read.

### Tie group { #tie-group }
Cells within the floor of the group's cheapest member. Inside a group the overage orders the
cells; across groups the score does. A rank inside a tie group is the tie-break's.

### Discriminating { #discriminating }
The ranking's verdict that the score separated the cells at all — false when every cell landed
in one tie group. A result, not a failure; but not a ranking either.

## Layout & geometry

### Aisle { #aisle }
A row of the warehouse the picker sweeps end-to-end.

### Bin { #bin }
One storage slot inside an aisle, at physical offsets `x_phys` and `y_phys`.

### BinKey { #binkey }
`(handling, category, storage_size, unit_type)` — the bucket a unit must be stored in.

### D — travel cost { #d }
Entrance-relative travel time to a bin: $D_b = x_{\text{pace}}\,x_{\text{phys}} +
y_{\text{pace}}\,y_{\text{phys}}$.

### Height bracket M(y) { #height-bracket }
An ergonomic multiplier on the whole at-location pick, keyed to shelf height.

## Demand & workload

### f_s — relative (pick) frequency { #f-s }
A SKU's pick-selection weight as a [0,1] relative share (`relative_frequency`).

### Batch — wave { #batch }
One picking wave, released once per site day per channel; a set of SKUs sampled by demand and
affinity at the channel's derived wave fraction.

### Task { #task }
One aisle's worth of a wave: the ordered sweep through the bins a picker visits in a single
aisle. Handed out longest-first (`lpt`) in every cell.

### W — task labor { #workload }
The realised time to clear one task: handling + travel + cart. Full definition on the
[Formula reference](formula-reference.md#task-labor).

### Line floor — base stock { #line-floor }
The replenishment regime this run declares: a SKU holds about one of its own mean order lines,
and every pick reorders what it took, so replenishment is a trickle in lockstep with picks. It is
what keeps trailers dispatching continuously.

### Planned lines — w_s { #planned-lines }
The batches of the run's forty-day script that ask for SKU $s$: the weight the placement score
gives it. 24,725 store lines and 119,223 fulfillment lines in this run, over a 400,000-SKU
catalogue.

## Placement

### Initial layout — uni / opt { #initial-layout }
How the warehouse is stocked once before day 1: **uni** = uniform-random fill; **opt** =
policy-stocked through the arm's own placement rule. The initial stock does not pass through
the yard.

### Assignment function (placement rule) { #assignment-function }
The rule that places arriving units into bins. This run holds it fixed at Experiment 8's winners
— `rank_cartlabor` (store) and `rank_minlabor` (fulfillment) — with `fifo` as the rider. See the
[Formula reference](formula-reference.md#the-families) for the catalogue.

### Put-away { #put-away }
The walk and handling that carries an emptied trailer's units to the bins the placement rule
chose. Timed and recorded in this era, beside pick labour.
