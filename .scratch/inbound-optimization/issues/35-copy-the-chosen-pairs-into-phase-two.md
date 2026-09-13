# Copy the chosen rule pairs into phase 2

Type: task
Status: open
Blocked by: 20

## Question

Copy phase 1's two hand-off values out of `restock_selection.json` and into
`Optimization/config/whatif_config.py`, which is the last edit between here and the
phase-2 launch. Both are declared `None` today and REFUSED rather than defaulted, so
this is a copy with no judgement in it — the judgement was 08's (the funnel's shape),
phase 1's (the ranking), and 20's (which families the evaluator can price).

From `comparison_20260913_113512/restock_selection.json`:

```python
PHASE2_PAIRS = [('rank_cartlabor', 'rank_minlabor'),
                ('rank_minlabor', 'tmin'),
                ('rank_labor', 'rank_labor'),
                ('tmin', 'rank_cartlabor'),
                ('rank_random', 'rank_popularity'),
                ('fifo', 'fifo')]          # the mandatory rider, already in `chosen`

PHASE2_STAFFING_PIN = {'mixed_20260816_131535__mixed_realistic_bell_lt0': '0ed2dd1582af'}
```

Read both off the artifact rather than off this file: a hand-typed pair list is the one
thing `validate_spec` cannot check against phase 1, and the pin is a digest.

Why this is a ticket and not a line in the launch: `validate_spec` refuses a phase-2 spec
whose `rule_pairs` name a family outside `Inbound.gain.FAITHFUL_GAIN_FAMILIES` while any
cell names a gain policy, so until
[Extend the gain bundles](20-extend-the-gain-bundles.md) landed, this copy put a spec in
the tree that refused at build. With 20 resolved the refusal is satisfied by all six
pairs; this ticket is what proves it, by building the spec.

What "done" looks like:

- both constants carry the artifact's values, with a comment naming the run root they came
  from (the run is the provenance; the values alone are not);
- `validate_spec` builds the `inbound_policies` spec without refusing — the check that all
  six pairs are now faithful, run rather than argued;
- `channel_restocks_for` derives the per-channel arm sets from the pairs (never authored
  beside them) and they match the artifact's `channels.<ch>.chosen`;
- `Tests/unit/test_campaign_cells_can_run.py` and `Tests/unit/test_funnel_window.py` still
  pass — several of their fixtures assume the constants are `None`, and any that no longer
  can must be reshaped rather than deleted.

Then phase 2 launches: 120 coupled units, 30.8–35.3 h of unit-seconds, ~164 GiB, ~8.6–9.7 h
wall at 4 workers (31's clock; do not mix with 24's). After it, publish.

## Comments

2026-09-13, from resolving [Extend the gain bundles](20-extend-the-gain-bundles.md): this
ticket exists because 20's resolution made it takeable and nothing else stands between the
map and phase 2. One caveat rides along, from the ranking rather than from the copy: store's
top three are separated by 0.005% and 0.12% and fulfillment's top eight span 0.73%, and the
pairing is rank-aligned — so the diagonal above is one of several equally defensible draws
rather than a derived optimum. It is a caveat the campaign publishes, not a reason to
re-pair; the campaign's real signal is the 6.1% / 8.0% gap down to the order-blind `fifo`
control, and the inbound comparison is within-pair across cells.
