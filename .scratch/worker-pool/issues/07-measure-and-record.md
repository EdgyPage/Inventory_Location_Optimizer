# 07 - Toy digest, throughput, memory; architecture/context/memory regen

Type: task
Status: claimed

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
