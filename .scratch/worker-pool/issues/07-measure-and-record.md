# 07 - Toy digest, throughput, memory; architecture/context/memory regen

Type: task
Status: resolved

Run the _toy_priced toy from a HEAD snapshot and digest it against comparison_whatif_20260919_170759; read throughput per arm from runtime_metrics; run the tiny smoketest for flatness on a real spawn pool; regenerate the architecture and context layers; update the memory store.
## Comments

- 2026-09-19 18:49: candidate toy run from snapshot 53964420, root comparison_whatif_20260919_184523, exit 0.
  `run_digest.py --self-test` OK; `run_digest.py <baseline> <candidate>`: IDENTICAL on the comparable surface (40 arms).
- Flatness read off run.log: the first cell-2 `done` line (18:48:19) precedes the last cell-1 `done` line (18:48:21);
  ONE `Log listener started` for the run (the baseline had two, one per cell); the analysis pool queued 56 graph jobs at once.
- Per-arm total_s: baseline 110.6 s over 40 arms, candidate 117.6 s -- NOT a measurement: the candidate ran while the e2e
  tier and the unit tier were running on the same host (memory toy-run-noise-floor-is-three-percent). Re-take on a quiet host
  if a number is needed; the pool changes no arm's work, only when it starts.
- The precompute pool sized itself to the spare cores (`workers=18` on 24 cores with 6 sim workers), as designed.
- 2026-09-19 19:55: the measurement RE-TAKEN on a quiet host (the earlier candidate ran
  beside two test tiers, so its per-arm seconds were not a measurement). Same toy, same
  six workers, `--no-analyze`; matrix wall read from the banner to the last arm's `done`
  line, worker-seconds from `runtime_metrics`:

  | run | arms | worker-sec | slowest arm | matrix wall | utilisation |
  |---|---|---|---|---|---|
  | baseline, cell-serial | 40 | 110.6 | 5.2 s | 118 s | 0.16 |
  | candidate, flat pool | 40 | 105.8 | 4.8 s | 93 s | 0.19 |

- **21% off the matrix wall, and that is the SMALL end of the effect.** The toy runs 20
  units a cell on a six-worker pool, so the old per-cell pool was never starved; what the
  flat pool removes here is only the wait at the single cell boundary. Utilisation stays
  low in BOTH runs because this toy's wall is dominated by the parent's serial setup
  (~50 s a cell of asset build + staffing derivation) against 3-5 s units -- the inverse
  of the campaign shape the pool was built for (4 units a cell, 12 workers, 3 h units,
  where 8 of 12 workers idled for the life of every cell). A toy cannot demonstrate the
  24 h -> 4 h claim; it can only show the pool is flat and costs nothing, which it does.
- **What the parent pays**, from the new `[pool] parent holds N cell(s)` line: peak RSS
  665 M holding one cell's assets, 707 M holding two -- ~42 MB for the second cell on an
  8,000-SKU catalogue, with the affinity CSR already dropped at submit. It scales with the
  catalogue, so re-read the line on a campaign-scale run before widening a matrix.
- Standalone re-analysis (`python -m Optimization.analyze_run <root> --workers 6
  --granularity graph`) exits 0 on the candidate, and its rendered artifact SET is
  IDENTICAL to the baseline's (369 files each, figures excluded from byte comparison).
  The `[access]`/`[era]`/`[render]` summaries print once per cell, tagged with the cell.
- Every tier re-run on the final tree: unit 3060, integration 464, e2e 60, architecture 422
  (one pre-existing failure deselected, see the map's Fog). The tiny smoketest passes all
  seven stages.
