# Decide the futuresight family's place in the campaign

Type: grilling
Status: open

Surfaced 2026-09-12 by [Measure what a gain cell actually costs](31-measure-what-a-gain-cell-costs.md),
which went to price `fsight_wall` and found it cannot run.

Blocks phase 2: two of its ten declared cells currently fail at their first drain, so the
campaign cannot be launched as declared whichever way this goes.

## Question

**Build the window zip, or drop the futuresight family from phase 2?**

`Inbound/site_space.py:98-113` refuses to compose two futuresight windows. Every phase-2 cell
couples, so `fsight_w5` and `fsight_wall` both die at their first drain -- proven on a probe run
(31, asset `31-fsight-coupled-refusal.md`) and, for the w=5 half, by calling `compose_site_view`
directly. The refusal is deliberate, documented and unit-tested; it declines to SHIP the zip
unexercised, which was correct when no arm needed it. An arm needs it now.

### What each side costs

**Build it.** The rule is already written down on the refusal: zip the two window tuples BY
BATCH INDEX (one batch is one site day, and the staffing derivation refuses channels with
different batch counts), then union each pair of `{sku: qty}` dicts, which are disjoint because
no SKU belongs to both leaves. So the change itself is small. What is NOT small, and is the real
question:

- the disjointness is currently an ARGUMENT, not an assertion -- decide whether the zip checks
  it or trusts it, and say why;
- 06's Tier-1 obligations (equivalence + sabotage) apply, as they did to every gain family;
- the unexercised-path objection the refusal was written for does not disappear: `futuresight`
  is the declared-unlawful reference arm, so the zip is exercised by exactly one cell family and
  by whatever tests are written for it;
- and the two cells' RUNTIME is still unmeasured and unbounded on paper (`_window_rates`
  re-aggregates the whole remaining script once per DRAIN). 31 measured the runnable poles at
  1.6-1.9x an unpriced cell and could bracket futuresight only from BELOW. The legal fix if it
  bites is memoizing `_window_rates` on `demand_v` -- 06's "legal-keyed-not-built" case -- which
  is a second build hiding behind the first.

**Drop it.** The family is the declared-unlawful upper-bound reference (objective resolution,
10): w batches of read-ahead, w=inf the oracle, never recommendable. Dropping it costs the
campaign its answer to *how much of the achievable gain the lawful arms capture* -- the H grid
still says which lawful policy wins and by how much against FIFO, which is the campaign's own
question, but nothing then bounds how much was left on the table. Phase 2 becomes 8 cells,
96 units, 24-27 h and ~132 GiB (31, section 6).

### What this ticket must produce

A decision, and if it is "build", the zip graduates as its own task ticket rather than being
done here. Either way, say what the campaign PUBLISHES about the missing or present upper bound
-- a campaign that quietly has no oracle arm reads as though none was ever wanted.
