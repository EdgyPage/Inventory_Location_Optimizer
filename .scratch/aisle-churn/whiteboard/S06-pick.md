# S06 — pick labour: the closed form against the simulator, by term and by placement

**Question.**  What does a day's picking cost in closed form, where is the closed form wrong, and —
for the study — does it price the DIFFERENCE between placement rules right?

**Derivation.**  `Optimization/simconfig/expected_travel.py` (the chain `accumulate → routing →
expected_swaps → expected_pick`), the per-line law being `PickConfig.closed_form`:

$$ W = \underbrace{n \sum_s \pi_s \sum_{b} \Pr(\text{reach } b)\, M(y_b)\left(I + \mathbb{E}[u_b]\,(p + v_s)\right)}_{\text{handling}}
 + \underbrace{\frac{\mathbb{E}[x]}{12 v_x} + \frac{\mathbb{E}[y]}{12 v_y}}_{\text{travel}}
 + \underbrace{c_{\mathrm{swap}}\,\frac{V}{\mathrm{cap} - \mathbb{E}[v] + \mathbb{E}[v^2]/2\mathbb{E}[v]}}_{\text{swaps}} $$

with bins reached in drain order, E[x] the expected farthest column per visited aisle (two-way)
or the lane length (one-way), and visit probabilities Poissonised.  Three variants on each
keyframe's placement (`assets/s06_pick.py`): (a) as the run records it; (b) with the simulator's
drain order (smallest on hand first, ADR-0003); (c) (b) with each SKU weighted by its lines in
the SAME batches the realised cost is read over.

**Measurement.**  40k fill root, pick stage, keyframes at 100 and 125, seconds per unit:

| section | arm | window | realised | (a) recorded | (b) drain | (c) drain + window rates |
|---|---|---|---|---|---|---|
| fulfillment | fifo | 100–124 | 18.64 | −4.4% | −3.7% | −5.2% |
| fulfillment | fifo | 125–139 | 19.25 | −6.6% | −5.6% | −5.8% |
| fulfillment | rank_minlabor | 100–124 | 18.23 | −2.0% | −1.1% | −2.8% |
| store | fifo | 100–124 | 90.05 | **+16.5%** | +16.8% | −7.9% |
| store | fifo | 125–139 | 116.29 | **−9.6%** | −9.3% | −8.2% |
| store | rank_cartlabor | 100–124 | 86.27 | +16.6% | +16.8% | −8.2% |
| store | rank_cartlabor | 125–139 | 111.00 | −9.1% | −8.9% | −8.6% |

**Residual 1 — the store's per-unit cost is dominated by WHICH SKUs a window asks for.**  Realised
cost moves 90 → 116 s/unit between two windows of one run (+29%): the store's handling term is
steep in weight (pow 1.5) and a window holds only ~1,500 lines, so a few heavy lines decide it.
The line-share expectation (a) cannot follow that; conditioned on the window's own SKUs (c) the
error is STABLE, −8% in both windows.  Revision: every pick-side comparison below conditions on
the realised script (the sampler's own draw), never on the line share.

**Residual 2 — the term split** (`assets/s06_terms.py`, fifo arms, batches 100–124, per day):

| term | store: realised → closed form | fulfillment: realised → closed form |
|---|---|---|
| units | 655 → 632 (−3.5%) | 3,259 → 3,222 (−1.1%) |
| tasks (aisle visits) | 59 → 68.5 (**+16%**) | 156 → 156 (0%) |
| travel s | 4,284 → 4,494 (+4.9%) | 14,482 → 16,525 (+14%) |
| swaps | 17.0 → 15.1 (−11%) | 87.8 → 75.1 (−14%) |
| handling s | 49,623 → 43,411 (**−12.5%**) | 23,087 → 21,783 (−5.6%) |
| total s | 58,995 → 52,447 (−11%) | 58,631 → 56,339 (−3.9%) |

Named terms:
- **Carry:** a line larger than the shelf holds is served in part and RE-OFFERED next day (nothing
  is lost under the era); the closed form truncates it at the stock on hand, missing those units
  (−3.5% / −1.1%) and their second visit.  Most of fulfillment's handling gap.
- **Affinity clustering:** the sampler draws partners together, so a store day touches 16% fewer
  aisles than independent bins predict (the store's travel is then over- not under-priced).
- **Cart carry:** a picker's cart carries across tasks within a batch, and tasks start part-full;
  the fluid swap count misses 11–14% of swaps.
- **Store handling, −9% beyond carry: unresolved** (candidates: the height mix of the bins
  actually drained, the weight tail of the window's lines).  Accepted as named for now.

**The decision-model finding.**  What the churn study needs is the GAP between placement rules,
not the level:

| section | window | realised gap (rank vs fifo) | closed-form gap (c) |
|---|---|---|---|
| store | 100–124 | −4.2% | −4.5% |
| store | 125–139 | −4.5% | −4.9% |
| fulfillment | 100–124 | −2.2% | +0.3% |
| fulfillment | 125–139 | −1.6% | 0.0% |

The closed form prices the store's placement gap within half a point: it is a HEIGHT gap, which
the handling term sees.  It misses fulfillment's entirely: there every bin is below 96 in (M = 1)
and the lane is one-way (x-travel = the lane length whatever the bin), so the only placement
lever is WHICH AISLES a day's lines land in — task count and swaps — and the closed form's
independent-bin routing cannot see aisle choice (memory
`expected-travel-closed-form-is-an-asymmetric-check`).  S07 builds the marginal value of a
location to price exactly that.

**Next.**  S07 (g_b), then S08/S09 on churn.
