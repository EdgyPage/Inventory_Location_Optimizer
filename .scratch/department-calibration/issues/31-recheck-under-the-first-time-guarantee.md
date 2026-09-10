# Re-check the reference pair under the first-time guarantee

Type: task
Status: resolved
Blocked by: 29, 30

Graduated 2026-09-08 from
[Fit the store's window to its own steady state](27-fit-the-store-window-to-its-steady-state.md),
decision 10. Task; the run is launched detached (memory `launch-long-drivers-detached`) and read
through the equilibrium report. Skills: none beyond the map's.

## Question

Fulfillment passed the 2026-09-08 checks on a script 6.7% lighter than its expectation, and the
store failed on labour overflow that a crew sized on served units could never absorb. Neither
leaf has been read under a derivation that promises anything. Take one 40-day era run on the
reference pair (`bell_lt0`, fifo, one release per day, 20-39 measured) under 29's derived crews
and floor, and read both leaves through 30's clauses.

**Read, per leaf:** `supply` level and trend against the stamped `1 - fill`; `labour` cut share
against the stamped expectation, standing labour carry bounded and not trending; `utilization`
against the DERIVED expectation (~0.72 store); `released_late` ok; receiving and put-away bands
(their rho unchanged; a put backlog is reported, not failed). Quote the derived crews and floor as
stamped, and the sampled script's per-day cv against the declared law.

**Also read**, because 27 could not explain them:

- `work_events` carries 77,544 `receive` rows (16.2 M s) on the store leaf of
  `comparison_20260908_094846` despite `recv_crew_size 0` -- find what writes them and whether
  they are priced into any clause.
- The fill/floor fixed point: 29 solves `floor_lines` on the catalogue with `n` held at the
  record's value; confirm the fielded warehouse (bins, aisles) and the stamped fill after the
  levels move (~19% more stock than the 2.51 M / 1.84 M units of the 1.0-line runs).
- The own-bin share and free-index depth under the larger floor (the ranked-arms / banding fog
  patch on the map sharpens on this reading).

## Done when

- Both leaves read in band on every clause, OR the failing clause is named with its numbers and a
  new ticket is graduated -- the era's numbers stay PROVISIONAL and 05's hold on the inbound
  funnel stands until this reads clean.
- The map's Decisions-so-far records the readings; if both leaves pass, the answer states
  explicitly that the hold lifts and the inbound map's 23/24 may proceed.

## Answer

Resolved 2026-09-09. One 40-day era run on the reference pair (`comparison_20260909_204522`:
the 2026-09-08 command verbatim -- `--spec _canary_single --shift-drain-or-cap --n-batches 40`
on `catalogue_reference_lt0` -- under 29's derivation and 30's clauses; launched detached,
`run.log` clean: no Traceback, no dead arm). **Both leaves read in band on every clause over
days 20-39. The era's numbers stop being provisional and 05's hold on the inbound funnel
LIFTS: the inbound map's "Verify the derived receiving crew" (23) and "Resize the funnel in
site days" (24) may proceed.**

### The verdicts (days 20-39, fifo; `Diagnostics/equilibrium_report.py --window 20-39`)

| leaf | labour: cut share vs stamped (band) | carry max | drained / capped | utilization pick / put / recv (realized / expected) | supply vs stamped | rework |
|---|---|---|---|---|---|---|
| store | 0.0227 vs 0.0212 (+0.0015, ±0.0337) | 679 u = 0.08 day | 8 / 12 | 0.791/0.756 · 0.398/0.368 · 0.728/0.665 | 0.026 vs 0.025 (+0.001) | 0 repacks; own-bin 0.000; free floor 1,450,954 |
| fulfillment | 0.0130 vs 0.0254 (-0.0124, ±0.0326) | 6,292 u = 0.17 day | 17 / 3 | 0.811/0.763 · 0.462/0.474 · 0.176/0.181 | 0.027 vs 0.025 (+0.002) | 0 repacks; own-bin 0.000; free floor 1,399,264 |

`released_late` ok on both (max lag 1,915 s store / 211 s fulfillment, all behind capped
days). Trends: cut share -0.015 / -0.013, supply -0.000 / -0.001. Against the 2026-09-08 store
leaf (cut share 0.2207 trending -0.181, carry 3,505 units, 0/20 drained) this is the same
script under a crew sized on demanded units.

### The stamped derivation (the run's own record)

- **Crews**: store **K = 31** (29 predicted ~32: `s_pick` priced at 105.51 s/unit on this
  script, not 108.4), fulfillment **K = 23**; put 60, receiving 22 (rho unchanged). Expected
  cut share 0.0211 / 0.0248 against the 0.0253 bound; expected utilization 0.724 / 0.815 at the
  declared demand, 0.756 / 0.763 at the sampled script (what the band is drawn on).
- **Floors**: **1.2728 (store) / 1.2668 (fulfillment) lines**, solved in 17 evaluations each for a
  first-pass fill >= 0.9747; stamped fill 0.9752 / 0.9747, expected missed share 0.0248 / 0.0253
  -- the levels the `supply` clause reads against, and both leaves land within 0.002 of them.
- **The fielded warehouse**: **2,466,650 bins over 2,761 aisles** (was 2,096,050 / 2,341:
  +17.7% bins, +17.9% aisles); stock **3,015,242 store + 2,220,097 fulfillment = 5,235,339 units**
  (was 2.51 M + 1.84 M = 4.35 M: **+20.3%**, the ticket's ~19%); 0 SKUs below their floor, 0
  above their declaration; 220,817 + 187,793 free bins at setup. Median order-up-to 13 (was 11).
- **The sampled script's day law**: fresh units/day store 6,395 mean, cv **0.280** over 40 days
  (0.238 on 20-39) against a declared 0.334; fulfillment 28,541, cv **0.211** (0.221) against
  0.250. Both sample UNDER the declared spread, by ~1.5 sd of a 40-day cv estimate -- reported,
  not judged; it is why both realized cut shares sit at or under expectation. The store's
  measured-window mean (6,522/day) runs 6.5% over the declaration (6,125), the fulfillment's
  (27,571) 9.8% under; the derivation prices the sampled script, so the bands already carry it.

### The three reads 27 could not explain

- **The `receive` rows are the era's DERIVED receiving crew.** `recv_crew_size 0` in the run
  spec is the FLAG-OFF declared key, which the era never reads
  (`sim_config.py:597-625`: the accessor takes the derived block first); the record's
  `derived.receiving.crew` is 22 (this run and the 2026-09-08 one alike). The rows are one
  unload per reorder pack -- 74,735 rows / 251,015 units on the store leaf here (77,544 /
  249,640 on 2026-09-08), matching the put rows one for one -- and they ARE priced: the
  receiving utilization clause reads them (0.728 vs 0.665 expected, in band). Nothing was
  unexplained but the key's name.
- **The fill/floor fixed point** collapses to one round as 29 said (`n now 589 (+0.00%)`); the
  numbers above are its output.
- **Own-bin share and free-index depth under the larger floor**: own-bin share exactly
  **0.000** on every day of both leaves again; free-index floor **1,450,954 / 1,399,264 of
  2,466,650** (58.8% / 56.7% free; 57.2% on the smaller warehouse). Two shapes, two floors, the
  same reading -- graduated (see below).

### Graduated

- [Band the own-bin share and the free-index depth](32-band-the-own-bin-share-and-free-index.md)
  (grilling, HITL): the fog patch this ticket was to sharpen. Two fifo shapes read own-bin
  0.000 and ~57-59% free; whether the band is "exactly zero" (under ADR-0003 a top-up means a
  bucket's free index ran dry) and what free-index floor the era declares are now phrasable.

### Left where it was

- The ranked-arms steady-state patch and the interaction-effects patch stay fog: no ranked arm
  ran here (fifo only), and the coupling read needs a campaign arm.
- The architecture layer is stale (as after every build); the memory
  `nothing-is-lost-under-the-era` says the era is provisional and the hold stands -- the
  `memory-maintainer` should amend it to: CALIBRATED 2026-09-09, hold lifted, K = 31 / 23 at
  floors 1.273 / 1.267.

