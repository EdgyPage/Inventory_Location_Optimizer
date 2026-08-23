# {{ experiment().title }}

**Two levers, two outcomes, one run — at full production scale.** Where restock gets put away
(**placement**) decides how much work a day contains. Who picks what next (**scheduling**) decides
how fast that work clears. This experiment moves each lever separately across the full
**400,000-SKU catalogue** — of which the shared build stocks **~263,000 SKUs at capacity**
across both channels (the [lifecycle page](comparison-overview.md) carries what is recorded per
channel, which is bins, not SKUs) — and measures both outcomes, so the two never get conflated.

Both levers are rules inside dispatch software, not construction projects. The four that matter on
this page, in plain terms:

- **FIFO** — restock goes into the first free slot. The do-nothing baseline everything is
  measured against.
- **`Rank_labor` family** — restock goes where it least burdens the busiest aisle, so no single
  aisle becomes the slow one everyone waits behind.
- **`Map` family** — restock goes to a precomputed "ideal address" per product, so the layout
  drifts toward a planned map instead of wherever space happened to be.
- **LPT** (longest-processing-time) — hand pickers the biggest jobs first, so the crew finishes
  together instead of everyone waiting on whoever drew the heaviest aisle last. (Who carries
  those biggest jobs is a fair question with a concrete answer — the fairness box below.)

!!! success "The finding"
    - **Placement pays where supply is predictable.** With immediate replenishment, the
      `Rank_labor` family cuts store hands-on **pick-hours** by **~2.6 %** against FIFO
      (`Rank_cartlabor` — the same load-balancing rule scored with cart-swap effort included —
      and `Rank_labor` finish within a rounding margin of each other). **Put-away effort is
      outside that ledger, and the margin against it is thin** — see the box below before
      you plan around the 2.6 %. When restock arrives
      on unpredictable 0–5-batch lead times, the winner flips to the `Map` family and the prize
      shrinks to **~0.8 %** — placement can only save travel it can plan for.
    - **Scheduling speeds up both warehouses, under every supply model.** LPT clears the same
      day's store work in about **two-thirds of the elapsed time** (**+45 %** throughput, a
      modeled ceiling — the evidence box below says what the model leaves out) and **+5 %** in
      fulfillment — while hands-on work changes by at most **±0.07 %** across all 136
      comparisons. The speed-up costs nothing: the work itself did not change.
    - **The two gains are independent, and they stack.** One removes work; the other removes
      waiting. Adopt either without the other; together they compound.

!!! danger "How much put-away could cost before the placement gain is gone"
    The model prices picking, not restocking, and the placement lever is the one exposed to
    that: it works by choosing *which slot* an arriving unit goes to, so if a computed slot is
    farther from the dock than the nearest free one, the restock crew pays for the pickers'
    saving. Here is the size of the margin, from the run's own counts rather than an argument.

    <small>Provenance, since this box decides whether the placement lever is worth piloting:
    the pick-hours per wave are the store labor total in
    [`data/whatif_volume.json`](data/whatif_volume.json) divided by the run's 75 waves, and the
    2.6 % is the store row of the per-cell
    [`channel_rollup_summary.csv`](data/k1_off_lpt/channel_rollup_summary.csv). Both per-wave
    counts — `mean_batch_prod_hours` 1.678 and `mean_reorder_placements` 11,225 on the FIFO row
    — are in the committed
    [`per_run_summary.csv`](data/k1_off_lpt/mixed_20260816_131535__mixed_realistic_bell_lt0/store/per_run_summary.csv)
    for this leaf, so every term of the exposure divides two numbers you can open. The 39-unit
    restock order is a model input, not a measurement — see the substitution above.</small>

    On the store channel a wave costs about **1.7 hours** of hands-on picking, and the winning
    rule saves **2.6 %** of it — roughly **2.7 minutes per wave**. That same wave puts away
    about **11,200 units**. Spread across them, the entire saving is worth about **0.014
    seconds per unit put away**.

    Turn that into trips with your own number, because ours is a model input rather than a
    measurement: **exposure per trip ≈ 0.014 s × (units your restock crew carries per trip)**.
    The simulator restocks in orders of about 39 units, which puts it at roughly **half a
    second per trip**; a floor running 100-unit pallets is nearer a second and a half. Either
    way the order of magnitude is the point, and it is *seconds*, not minutes.

    Read that as the honest bound it is: **the placement gain survives only if the new slotting
    adds essentially nothing to the average put-away walk.** It does not mean the gain is
    illusory — there are real reasons to expect slotting to help restock too (the same rules
    bias toward near, low bins, and a unit is put away once but picked from many times) — but
    those reasons are arguments, and this number is the exposure. It is why the pilot's
    restock-crew hours are a *primary* measurement rather than a nice-to-have, and why a
    placement pilot that does not measure them cannot tell a win from a transfer. The
    scheduling lever carries no equivalent exposure: it changes the order tasks are handed
    out, and touches no slot.

**Two warehouses, not one.** The experiment runs two independent simulated buildings over the
same catalogue, and every finding names which one it belongs to:

| | the work looks like | this experiment's lever |
|---|---|---|
| **store** channel | replenishment: big carts, long sweeps down each aisle — a retail backroom or DC | placement **and** scheduling |
| **fulfillment** channel | e-commerce piece-picking: small totes, short frequent trips | scheduling only |

A real network usually operates both kinds of building — apply each finding to the buildings
whose work looks like its channel. (In the simulation the two channels share one physical
build but operate fully disjoint racking and independent order streams — the
[lifecycle page](comparison-overview.md) states exactly what is and isn't shared, including the
one caveat for DCs where both channels pick from the same slots.) Every other term is one click
away in the [glossary](glossary.md).

## The quick read

One inventory, one warehouse, four ways to run it. Hands-on hours are the work; elapsed hours are
when the day's work was finished:

| | do-nothing placement + naive schedule | best placement + LPT schedule | change |
|---|---:|---:|---:|
| hands-on pick-hours | 125.9 | 122.5 | **−2.7 %** (placement) |
| elapsed hours to clear the run | 8.09 | 5.42 | **−33 %** (mostly scheduling) |
| items picked | 2,535,417 | 2,535,381 | same work |

<small>Store channel, `bell_lt0` inventory: `uni_fifo` under round-robin vs `opt_rank_cartlabor`
under LPT. The variable-lead-time inventory reads the same direction with a smaller placement
term — the [labor page](full-results.md) spans both. Arm names read
`<starting layout>_<placement rule>`: `uni` = random start, `opt` = the rule's own ideal start
([glossary](glossary.md#initial-layout)). All numbers from the committed
[`data/whatif_volume.json`](data/whatif_volume.json).</small>

The two rows move for different reasons, and that is the whole experiment. Placement made the day
*smaller*; scheduling made it *denser*.

!!! question "How can the day end 33 % sooner if the work barely shrank?"
    Because pickers spend part of every batch **waiting**. Under a naive round-robin hand-out,
    whoever draws the heaviest aisle finishes last while everyone else stands idle. LPT hands the
    longest tasks out first, so the crew finishes together. No work was removed — the same ~123
    hands-on hours of picking happened — it was packed into fewer elapsed hours. Across all 68
    same-rule store comparisons the day ends **28.5–39.8 % sooner** (median **31 %**), and every
    single comparison is positive. That is why labor and throughput are different columns
    everywhere on this site.

!!! warning "What kind of evidence this is"
    This is a **controlled A/B experiment inside a simulation**: the same sequence of orders,
    replayed identically under 34 placement rules × 2 schedulers — a workload no real building
    could run twice. The units, in floor terms: a **batch is one wave of orders released
    together**, and the run is **75 consecutive waves** — the store's crew of **25 pickers**
    (fulfillment: 20) clears it in ~5–8 elapsed hours depending on the scheduler, i.e. roughly
    one simulated workday, across a modeled footprint of **384 aisles / ~398,500 bins**. The demand is an **ordinary steady period** drawn from the
    catalogue's own demand rates — not a peak, promo, or returns surge — so **pilot on ordinary
    weeks and treat peak behavior as unproven**: do not schedule the pilot's evaluation over a
    promo window, and treat any stress-day claim as outside this experiment's evidence.
    **It is also one draw, not many.** Every arm replays the *same* simulated demand sequence —
    that is what makes the comparisons exact, but it means this sweep contains no estimate of how
    much a different ordinary week would move the numbers. Two things bound that gap without
    re-running: within this week, each arm's result is a median over 75 waves whose spread is
    published as the interval on every row of Figure 1's table, so wave-to-wave variation is
    visible rather than hidden; and across weeks, the scheduler finding has now replicated on
    **three independently generated demand streams** (Experiments 6, 7 and 8) with the sign never
    once negative. The placement ranking has no such cross-stream replication yet — treat its
    *ordering* as this week's result until the pilot or another draw confirms it.
    That control is what a simulation buys; what it cannot buy is your building's exact
    numbers. Every "hour" is **modeled pick-time** (setup + handling + travel + cart swaps under a
    stated cost model), so absolute *levels* are model-scale — but the **percentage comparisons
    are exact**: the simulator is deterministic, each pair of runs replays the identical day, and
    the difference is a recomputation, not an estimate with error bars. The model's size is
    likewise a modeling choice: the mechanisms (waiting at the end of a batch; travel per pick)
    exist in any building, and it is the *direction and ranking* of the results, not the third
    decimal, that transfers.

!!! note "This experiment is the new baseline"
    The simulator's synthetic **demand stream was upgraded** before this sweep (a faster,
    deterministic order-draw engine — "v2", 2026-08-20). The weighting model is identical, but
    the specific sequence of simulated orders differs from earlier experiments, so numbers here
    are compared **within this experiment**, and future sweeps will be compared against **these**
    figures. Experiments 1–7 remain readable history on the previous stream; do not lay their
    absolute numbers beside these.

!!! note "The charts on this page were rebuilt on 2026-08-23"
    Same run, same simulation, same numbers — **the presentation changed**. The analysis suite was
    redesigned so that every comparison carries a percentage against the baseline, an effect size
    and an interval, in a unit a person reads (hours, or seconds where a task is seconds). Charts
    that plotted the demand curve rather than the effect were dropped, and the figures that rank
    every arm now publish their numbers to a table you can open beside them. If you read this page
    before that date, the conclusions are unchanged; the evidence behind them is easier to check.

## Lever 1 — placement: less work to begin with

<figure markdown>
  ![Top runs vs FIFO — labor and throughput](images/{{ experiment().run }}/{{ experiment().inventories.bell_lt0.id }}/store/table_top_vs_baseline.png){ width=920 }
  <figcaption><strong>Figure 1.</strong> The top three store placement rules from each
  initial-layout family, measured against the do-nothing FIFO baseline on labor and throughput at
  once — immediate-replenishment inventory, where the <code>Rank</code> family (place each unit
  where it least burdens the busiest aisle) holds the podium. Each row carries its effect size
  and a 95% interval; with 75 paired batches the p column separates almost nothing, so the
  magnitude is what ranks the rules. Every arm's row, including the ones that did not place,
  is in the run's <code>vs_baseline</code> table. Source:
  <code>table_top_vs_baseline.png</code> (store, <code>bell_lt0</code>, cell
  <code>{{ experiment().run }}</code>).</figcaption>
</figure>

**Reading that table if you are not a statistics person:** the first two columns are the answer —
how much work the rule removed, and how much faster the day went. The last three
(*Hedges g*, the *95 % CI*, *Wilcoxon p*) only say whether the difference is **real** rather than
how big it is, and here they all say yes; you can skip them. If you want them:
*g* is the size of the difference relative to how much it bounced around wave to wave (above ~0.8
is a large, consistent effect — these are around 3); the *CI* is the range the true value is very
likely in; *p* is the chance of seeing this by luck, and at 75 waves it is tiny for everything,
which is exactly why it does not rank the rules. Full definitions are in the
[formula reference](formula-reference.md#comparison-statistics).

Read the labor column as *how much work the rule removed* and the throughput column as *how much
faster the day went*. **Read both before you pick a rule.** The three `Rank` variants save almost
the same labor (+2.2 % to +2.6 %), but they do not buy the same speed: `Rank_cartlabor` and
`Rank_labor` add **+3.2 % to +3.6 %** throughput, while `Rank_minlabor` adds only **+1.2 %** on
the uniform-start arm and *loses* **−3.1 %** on the optimal-start one — it spreads work to the
least-loaded aisle rather than the nearest one, which evens the load and lengthens the walk. If
you pilot one rule, pilot `Rank_cartlabor` or `Rank_labor`. Every arm's row, with its interval,
is in the run's `vs_baseline` table beside this page.

Two further boundaries matter, and each is a finding rather than a failure:

- **The fulfillment channel barely moves** (best rule: under 0.3 %): small totes and short trips
  leave placement little travel to save.
- **Unpredictable supply shrinks the prize and changes the winner.** On the 0–5-batch-lead-time
  inventory the best any placement rule saves is **~0.8 %**, and the leader is the `Map` family,
  not `Rank_labor` — when you cannot know what arrives next, a fixed ideal-address map beats
  reactive load-balancing. The [labor page](full-results.md) shows both inventories side by side.

## Lever 2 — the scheduler: the same work, finished sooner

<figure markdown>
  ![Cumulative volume vs elapsed time, round-robin vs LPT](images/{{ experiment().whatif.scatter }}){ width=920 }
  <figcaption><strong>Figure 2.</strong> Items picked so far (y) against elapsed hours (x), one
  panel per channel, each holding its own placement rule fixed and varying only the
  <strong>scheduler</strong> (the two panels do not use the same rule — each names its own). The slope
  is throughput; the dot is the finish. Both lines reach the same height — the same work — but the
  LPT line is steeper and stops sooner.
  <br><br>
  <strong>The two panels are not on the same scale, and that is real, not a units error.</strong>
  Fulfillment clears roughly <strong>5–6 million items in about an hour</strong>; the store clears
  roughly <strong>2–2.5 million in five to eleven hours</strong>. Fulfillment's modeled pick is an
  order of magnitude cheaper per item — small carts, short trips, and a per-channel cost model
  whose handling term grows logarithmically rather than steeply
  (<a href="formula-reference.md">formula reference</a>) — so its rate runs about
  <strong>12× the store's</strong>. Compare each panel's two lines with each other; do not compare
  heights across panels. Source: <code>whatif_volume_curves.png</code>; ranges from
  <a href="data/whatif_volume.json"><code>data/whatif_volume.json</code></a>.</figcaption>
</figure>

This is the scheduler argument in one picture. Nothing about the warehouse, the catalogue, or the
placement changed between the two lines — only the order tasks were handed to pickers.

One further comparison sizes the prize across the two levers, and it is **not** on the figure
above — that figure holds the placement rule fixed, so neither line is the one described here.
Taken from the run's own volume data: a store that changes *only* its scheduler (keeping
do-nothing placement) still clears work faster than one that adopts the *best* placement rule but
keeps the naive schedule — **451,856 vs 321,773 items/h**. If only one change is on the table,
change the scheduler.

<small>That pair reads from the committed [`data/whatif_volume.json`](data/whatif_volume.json):
`uni_fifo` under LPT vs `opt_rank_cartlabor` under round-robin, store, `bell_lt0`.</small>

!!! question "Who ends up carrying the biggest jobs — and where do the saved hours go?"
    Three floor questions the model cannot answer alone, stated plainly rather than skipped.
    **Can everyone actually take every aisle?** The model assumes a **fully cross-trained
    crew**: any picker may be handed any aisle, because that is the only way "whoever frees up
    takes the longest remaining task" works. Real floors are not like that — reach-truck and
    hazmat certifications, zone assignments, and individual physical restrictions all limit who
    is eligible for what. **Before LPT is switched on, the pilot has to define the eligible set
    per picker**, and the scheduler then picks the longest task *that picker may take* rather
    than the longest outright. That narrows the pool a freed picker draws from, so expect a
    smaller gain than the modeled one on a heavily zoned or certification-split crew — the
    modeled figure is the fully-cross-trained ceiling.
    **Fairness:** LPT ranks *tasks*, not people — it says the longest task goes out first, not
    who draws it. Combined with the assumption above, a pilot should adopt a rotation
    rule on day one; the default we propose unless the floor has a better one: **no picker
    draws one of the shift's three longest tasks on consecutive shifts.** Someone has to own
    that, or it is a sentence rather than a rule: the **dispatch lead** holds the list of who
    drew a top-three task each shift and it is read out at the start of the next one, so a
    picker can see their own history and say so when it is wrong — the pilot does not depend on
    the WMS being able to enforce it. **That rule alone is not enough, because the mechanism
    runs inside the shift, not between shifts.** LPT re-sorts at every wave release, and a
    shift is ~75 waves, so a consistently fast picker can draw the longest task again and again
    in a single day without ever breaking a consecutive-shift rule — which is exactly the "why
    does John always get the worst aisle" question a crew asks in week one. So the rule has a
    within-shift half: **no picker takes the wave's longest task more than twice in a row**; on
    the third the scheduler hands it to the next eligible picker and gives them the next-longest
    instead. It costs a little of the modeled gain, and it is the difference between a rule the
    floor accepts and one it works around. **Strain:** "longest"
    here is *time*, not physical difficulty — heavy items, bad reach heights, and congested
    aisles are not in the cost model, a long task is not necessarily a hard one, and the model
    cannot say how often the two coincide — which is exactly why the rotation rule above is the
    guardrail, not an afterthought. What the model cannot supply, the floor can: **week one of
    the pilot asks the crew directly** — which aisles actually congest, where the heavy and
    awkward items really are, and how the pace changes late in a shift — and those answers
    amend the rotation rule rather than being filed as feedback. **The reclaimed hours:** the model shows the same work
    ending ~31 % sooner; whether that becomes earlier truck cutoffs, more waves, training time,
    or earlier finishes is a management decision the pilot should announce **before** it
    starts, because the crew will ask on day one.

## Reading the figures together

<figure markdown>
  ![Lead over FIFO by placement rule, store](images/{{ experiment().run }}/{{ experiment().inventories.bell_lt0.id }}/store/percent_volume_lead.png){ width=920 }
  <figcaption><strong>Figure 3.</strong> The same cumulative-volume lens, now holding the scheduler
  fixed at LPT and varying the <strong>placement rule</strong>: how far ahead of FIFO each rule is
  at matched elapsed time, as a share of what FIFO had picked by then, with the dot marking where
  the arm finished the run — each curve is labelled at its finish, so no colour matching is
  needed. These are the same six runs as Figure 1's table. <strong>Two of the six run below zero
  for most of the day</strong>: the <code>Rank_minlabor</code> pair, which saves labor but does
  not convert it into a faster finish.
  <br><br>
  <strong>This chart and Figure 1 measure different things, and for one arm they disagree in
  sign.</strong> Figure 1's throughput column is a per-batch rate, paired wave against wave and
  summarised by its median; this chart is cumulative items at matched elapsed time across the
  whole run. <code>Uni|Rank_minlabor</code> is mildly positive on the first (+1.2 %) and negative
  on the second — it is a little quicker on the typical wave and still behind on the day, because
  the waves where it loses are the big ones. When the two disagree, the day-level view is the one
  that decides whether the shift ends sooner.
  Source: <code>percent_volume_lead.png</code> (store,
  <code>bell_lt0</code>, cell <code>{{ experiment().run }}</code>).</figcaption>
</figure>

(A **cell** is one full copy of the sweep under one scheduler setting — `k1_off_rr` is the
round-robin copy, `k1_off_lpt` the longest-first copy; comparing same-named arms across the two
cells isolates the scheduler.)

| Figure | Lever it isolates | Compared against | Cell | Inventory | Channel |
|---|---|---|---|---|---|
| **1** — takeaway table | placement | FIFO baseline | `{{ experiment().run }}` | `bell_lt0` | store |
| **2** — volume curves, rr vs LPT | scheduler | cell `{{ experiment().whatif.reference }}` | both | `bell_lt0` | store + fulfillment |
| **3** — volume curves by rule | placement | FIFO baseline | `{{ experiment().run }}` | `bell_lt0` | store |

<small>Run <code>{{ experiment().whatif.source_run }}</code> ({{ run_commit() }} — the commit
recorded by the run itself in its <code>run_spec.json</code> at simulation time, i.e. the exact
code checkout the simulator ran as):
{{ experiment().whatif.cells }} cells × {{ experiment().whatif.arms }} arms ×
{{ experiment().whatif.n_batches }} batches over the full catalogue. Every percentage on this page
is quoted from the committed <code>data/whatif_volume.json</code>,
<code>data/whatif_labor.json</code>, or <code>data/whatif_delta.json</code>; the figures are
rendered from the same run's databases by <code>Optimization/run_analysis.py</code>.</small>

## The ask

Nobody should re-slot a warehouse on a simulation's word — and nobody needs to. Both changes are
dispatch-software settings, pilotable in one building in weeks, with no capital and no
construction. What this experiment contributes is **which changes, out of 34 tested, are worth
that pilot** — and exactly what to measure:

1. **Both channels: dispatch the longest tasks first (LPT)** instead of dealing them out in turn.
   Modeled effect: the same workload clears in **~31 % less elapsed time** in store, **~5 %** in
   fulfillment — at zero labor cost, under every one of the 34 placement rules tested, on both
   supply models. This is the highest-confidence, lowest-effort change on the board.
   *What this means in the WMS:* when a wave's tasks are built, release them to pickers ordered
   by estimated task time, longest first, instead of round-robin or free pick — in most systems
   that is a task-release/priority setting on the wave template, not a custom build; if your
   WMS cannot sort task release by estimated duration, that gap is the first thing to confirm
   with the vendor. Apply the sort within whatever release unit your WMS actually dispatches —
   wave, sub-wave, or task group; the simulated unit was a whole-aisle task, and finer grains
   are untested (below). The estimate only needs to *rank* tasks, not predict minutes — and
   that is not hand-waving: the simulation's own +45 % was earned sorting on a modeled
   *estimate* (travel + handling + a cart proxy), not on realized times, so an estimator that
   gets the big tasks near the top captures the mechanism. Adopt the rotation rule from the
   fairness box above on day one — it is part of this ask, not an optional extra. Two things
   the model did NOT test, named plainly:
   dispatch at **finer grain** than whole-aisle tasks (if your waves split aisles across
   pickers, the magnitude is untested — but this sweep brackets it with evidence, not a claim:
   fulfillment's short, near-uniform tasks are the fine-grain end and gained **+5 %**, the
   store's whole-aisle tasks the coarse end at **+45 %**; the closer your task unit is to
   uniform-and-small, the closer to the low end you should plan), **floor congestion** when
   several pickers start their longest aisles at once (walking between aisles is not modeled
   for either scheduler), and **staffing swings** (the model runs a fixed, known crew — call-
   outs, late starts, and mid-shift changes are untested; LPT re-balances only at each wave's
   release against whoever is present) — make all three week-one watch items alongside the
   measurement plan below. On congestion specifically, "watch it" is not a plan, so here is the
   default posture we would run unless the floor knows better: **cap concurrent pickers per aisle
   at whatever the floor already treats as safe (commonly one for a narrow aisle, two for a wide
   one) and let the scheduler skip to the next-longest eligible task when the cap is hit.** That
   is the same shape as the eligibility rule in the fairness box and costs a little of the modeled
   gain; the alternative — releasing the longest aisles simultaneously and discovering the cap on
   the floor — costs more. The trigger to revisit it: pickers queueing at aisle entrances, or
   travel-time-per-pick rising against the baseline weeks. **What LPT does inside a wave matters for whether your WMS can
   reproduce this:** at each wave's release every task is handed out longest-first, and a picker
   who finishes early takes the longest task still unassigned *from that same wave* — the idle
   time the gain comes from is removed continuously, not at the next wave boundary. A WMS that
   only re-releases work between waves will not reproduce the modeled gain; the setting to look
   for is whether a freed picker pulls from the current wave's remaining pool.
2. **Stores with reliable replenishment: place restock with the `Rank_labor` family** instead of
   first-free-slot. Modeled saving: **~2.6 %** of hands-on pick-hours, needing only data any WMS
   already has (demand rates and slot positions).
3. **Stores living with variable lead times: pilot `Map`-based placement**, and expect a smaller
   prize (**~0.8 %**) — or pair the pilot with supply-reliability work, which this sweep suggests
   is itself a placement-value multiplier.
4. **Measure the pilot the way the experiment measures — success criteria and exit both named
   before day one.** Track three numbers: hands-on pick hours (did the work shrink?),
   **restock-crew hours** (did put-away pay for the placement gain? — the model cannot answer
   this, so the pilot must), and time-to-clear (did the day end sooner?). *What counts as a
   comparable week:* same day-of-week mix, no promo/holiday, order volume within ~10 % of the
   baseline weeks. *Duration:* at least four comparable weeks — enough for the placement effect
   to touch most fast-moving slots. *Success:* the modeled effects are ceilings, so set the bar
   beneath them with room for floor noise — e.g. time-to-clear improves by at least a third of
   the modeled 31 % (≥10 %) with pick + restock hours flat within ±1 %; agree your own numbers,
   but agree them in writing first. *Exit, and each lever has its own:* for the **scheduler**, any missed cutoff
   attributable to dispatch, or two consecutive days behind plan, reverts to the current rule that
   shift, no meeting required. For **placement**, the exit is the restock side, because that is
   where the risk lives: if restock-crew hours rise enough to consume more than half the measured
   pick-hour saving over any two comparable weeks, revert to first-free-slot put-away — and if
   they rise past the whole saving in a single comparable week, revert immediately rather than
   waiting for the second. Track the two exits separately: the levers are independent, so a
   placement rollback is not a reason to drop LPT, and the pilot should be able to run one without
   the other.
   Both changes are settings, so reverting is minutes, not a project — which is what makes this
   a low-risk pilot rather than a commitment.

## Sizing the prize in your numbers

The model deliberately reports **modeled hours and exact percentages**, never dollars — its
absolute hours are model-scale, and pretending otherwise would be false precision. Turning the
percentages into a dollar ask takes three numbers only your site has, so here is the arithmetic
with the blanks left honest:

- **Placement (the 2.6 %):** *annual store pick-hours × loaded rate × 2.6 %*. Note what the
  2.6 % is a percentage OF — pick-hours, not total site labor — so the pick share is
  load-bearing; if picking is 20 % of your labor budget this is a modest line, at 60 % it is a
  real one. *Worked, with numbers that are yours to replace:* a site running 200,000 pick-hours
  a year at a \$30 loaded rate is spending \$6M on picking, and 2.6 % of that is about
  **\$156k a year** — against which you must set whatever the pilot's restock-crew hours cost
  (see the exposure box at the top: the margin against put-away is thin, and unmeasured until
  the pilot measures it).
- **Scheduling (the ~31 %):** this one is **not a labor-dollar saving** — hands-on hours are
  provably unchanged — it is reclaimed *elapsed* time, and its value depends on which of three
  paths your site converts it into: **capacity** (more volume through the same shift),
  **service window** (later order cutoffs / earlier truck departures), or **overtime** (if your
  crews currently run past shift to clear the day, the compression comes straight out of OT
  hours — the one path that IS a direct labor-dollar line). Name the intended path before the
  pilot; the measurement plan then prices that path, not an abstraction.
  *Worked on the overtime path, again with numbers that are yours to replace:* a store crew of 25
  that currently runs **1 hour of overtime a day, 5 days a week** is buying 125 OT hours a week —
  at a \$30 base and time-and-a-half, about **\$5,600 a week, or \$290k a year**. The modeled
  compression is ~31 % of elapsed time, but the honest planning figure is the pilot bar, not the
  ceiling: at the one-third-of-modeled success bar (≥10 %) a shift that ran an hour over now runs
  roughly 6 minutes over, which is most of that OT line. Two conditions decide whether any of it
  lands: the overtime has to be *caused by the day not clearing* (if it is caused by volume
  arriving late, compression does not touch it), and the reclaimed time has to actually be sent
  home rather than backfilled with more waves — which is the path decision above, made in advance.
  On the capacity and service-window paths the same 31 % is worth real money too, but it shows up
  as revenue or as a later cutoff, and only your site can price those.

Why trust the *selection*, even without trusting the exact percentages: the scheduler gain is
{{ census_claim('thr_gain_vs_ref_pct') }} across this sweep — the third consecutive catalogue
stream where that holds ([Experiment 7](../experiment-7/index.md) and
[Experiment 6](../experiment-6/index.md) before it). The placement ranking is more sensitive: it
holds within a supply model but **flips between supply models** — which is exactly why the pilot,
not the simulation, should price your building.

## Go deeper

Ordered shallowest first — stop wherever the question is answered.

1. **[Throughput — the scheduler lever](comparison.md)** — how the rate was measured, the windows
   it was measured over, and the uplift across every placement rule.
2. **[Labor — the placement lever](full-results.md)** — the two-inventory split, the proof the
   scheduler doesn't touch labor, and every arm including the losers.
3. **[How a run works](comparison-overview.md)** — the simulation lifecycle end-to-end, and what
   the sweep holds constant versus varies.
4. **[Formula reference](formula-reference.md)** — the pick-time cost model, the labor
   decomposition, and every placement rule's scoring objective.
5. **[Inventory distributions](inventory.md)** — the catalogue and the two supply models.
6. **[Glossary](glossary.md)** — every term on these pages, each with a stable anchor.

!!! warning "Not comparable with Experiment 7 — but it is the stress test"
    [Experiment 7](../experiment-7/index.md) ran the same design on the same catalogue under the
    previous demand stream, at reduced scale. Its percentages differ because the simulated order
    sequence differs. Do not read 7→8 as a trend; read it as **the finding surviving a change of
    demand stream**: the scheduler gain replicated (all comparisons positive, again), the
    placement direction replicated where supply is predictable, and the sweep additionally
    exposed that the placement *winner* depends on supply reliability. Current numbers come from
    this page.
