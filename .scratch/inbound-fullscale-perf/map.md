Status: in-progress

# Full-scale inbound performance -- attach it, find the sinks, shrink the growth

## Destination

Full-scale (400k SKU, coupled site, 12-worker WorkPool) campaigns that finish on time:
inbound performance attached to every run as per-arm columns, the sinks named with
numbers, and every exact saving landed with its identity proof.

Opened 2026-09-24 from the user: "Run the evaluation testing suite on the new code to do a
comparative analysis and find where the performance sinks are. I want to attach
performance of the inbound operation so we can complete full scale runs timely. Keep an
eye out for opportunities to share caches and shrink growth curves algorithmically.
Maintain a whiteboard to preserve findings and attach performance rigorously."

Plan: `~/.claude/plans/tender-roaming-dream.md`.

## Decisions (the user's, 2026-09-24)

- (a) 400k budget: two reduced runs (A before, B after the optimisations), 20 batches.
- (b) Inbound timings become runtime_metrics DDL columns (schema pipeline).
- (c) Vectorise the balance pools' aisle scan in numpy; re-pin
  `test_placement_selection_is_not_a_scan.py` with the reason stated.

## Baseline commit and run roots

- Code baseline: 109891a1 (after c6e3ec54 replay, bc5671fe rank_sortmatch).
- Existing 400k roots: campaign `comparison_whatif_20260920_150203`; churn probes
  `_20260923_145701 / _164427 / _175345`.

## Sessions

| S | topic | prediction | outcome |
|---|---|---|---|
| S00 | anchors from existing roots and run.log | sim stage >= 1.6x its bound; analysis + parent setup >= 25% of wall | REFUTED for the campaign (last launch 1.03x its bound; O0 dropped); a single-cell 400k run spends 35% of its wall in parent setup, and that finding became O9 |
| S01 | I0 inbound instrumentation | toy digests IDENTICAL; overhead < 0.5% | MET: 6 cells IDENTICAL; overhead under the toy noise floor (de605170) |
| S02 | run A | gmyopic inb_yplan >= 60% of reord; fifo put_open >= 50% of reord; sib_setup >= 50% of the unnamed gap | |
| S03 | pull fraction | yard_pulls / yard_T in 0.3-0.5 | 0.255, below the band (better than predicted); O1 kept |
| S04 | O1 lazy yard plan | shadow IDENTICAL; gmyopic reord -35% or more | MET on the meso rung: 0 mismatches over 144 pulls; reord -58% (drain), -81% (asap), -24% (2-door gated) |
| S05 | O3 numpy boundary | pool opens >= 3x faster | MET: template opens 4.6x / 4.8x faster, eager 2.6x / 3.0x |
| S06 | O9 fill-curve memo (found in S00) | bit-identical; >= 2.5x on the curve | MET: 3.3x (358 -> 109 s per channel), bit-identical |
| S06b | O5/O4/O6/O7 | each IDENTICAL; together 3-10% of arm wall | |
| S07 | O8 analysis + O0 dispatch | analysis stage -30%; sim stage within 15% of bound | |
| S08 | run B + ladders | T exponent 3.13 -> <= 2.5 | |
| S09 | extrapolation | the 40-batch 11-cell campaign's predicted wall | |
| S10 | vectorisation survey: picking + assignment functions (added 2026-09-24, the user's) | >= 3 candidates each >= 1% of arm wall and data-parallel | |
| S11 | implement the survey's survivors, one commit each | each IDENTICAL; >= 1.5x on its own function | |
