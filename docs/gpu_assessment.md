# GPU acceleration assessment (measured)

Backends: numpy 2.0.2  |  cupy 14.1.1 (NVIDIA RTX A5500 Laptop GPU)  |  torch 2.11.0+cu130 (NVIDIA RTX A5500 Laptop GPU)

Fixed-seed CPU-vs-GPU microbenchmarks (`Tests/bench_gpu_*`), weighted by real
wall-share from the newest run.log (`Tests/bench_sections.py`).

## Wall-share (Amdahl)
- reord: 44%
- build: 41%
- db: 5%
- pre: 4%
- sim: 2%
- extr: 2%
- inv: 1%

## Verdicts
- sampling   section=build  share= 41%  best GPU=      2.4x  (n/a)  -> whole-run 1.31x
- placement  section=reord  share= 44%  best GPU=     26.8x  (equiv)  -> whole-run 1.74x
- picksim    section=sim    share=  2%  best GPU=     39.5x  (equiv)  -> whole-run 1.02x

## Notes / constraints
- Sampling (`build`) is the biggest share but the draw is sequential with a per-step host sync; a faithful GPU port matched CPU exactly but a one-shot static Gumbel-top-k diverges (drops dynamic partner lift) and is not adoptable without re-baselining.
- Placement greedy select + dynamic affinity stay sequential; only the static cost matrix is GPU-parallel.
- Pick sim is ~5% of wall (low ceiling).
- Determinism: any GPU RNG changes the batch stream; adopt only if all arms use the same deterministic sampler and metrics are re-baselined.
- See `Tests/_gpu_bench_results.csv` for the full per-size table.
