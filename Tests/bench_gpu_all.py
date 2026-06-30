"""bench_gpu_all.py - run the full CPU-vs-GPU benchmark suite (sampling / placement / picksim),
weight each kernel's measured speedup by its real wall-share (bench_sections / Amdahl), print a
summary, and write Tests/_gpu_bench_results.csv + docs/gpu_assessment.md.

Backends auto-detected (CuPy + PyTorch when available; CPU always).  Run:
    python Tests/bench_gpu_all.py
"""
from __future__ import annotations

import csv
import os

import bench_gpu_common as B
import bench_gpu_sampling
import bench_gpu_placement
import bench_gpu_picksim
import bench_sections

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# kernel-prefix -> the wall section it would accelerate (for Amdahl weighting)
_SECTION_OF = {'sampling': 'build', 'placement': 'reord', 'picksim': 'sim'}


def _best_speedup(r):
    gpu = [g for g in (r['cupy_ms'], r['torch_ms']) if g and g > 0]
    return (r['cpu_ms'] / min(gpu)) if (gpu and r['cpu_ms']) else None


def _amdahl(share, speedup):
    """Whole-run speedup if a section of fractional wall `share` is sped up by `speedup`."""
    if not share or not speedup:
        return 1.0
    return 1.0 / ((1.0 - share) + share / speedup)


def main():
    print('=' * 100)
    print('GPU benchmark suite -', B.describe_backends())
    print('=' * 100)

    print('\n[1/4] Section (Amdahl) baseline')
    shares = bench_sections.run()

    rows = []
    for name, mod in [('sampling', bench_gpu_sampling), ('placement', bench_gpu_placement),
                      ('picksim', bench_gpu_picksim)]:
        print(f'\n[{name}] running...')
        try:
            rows.extend(mod.run())
        except Exception as exc:                                   # noqa: BLE001
            print(f'  {name} bench failed: {exc!r}')

    B.print_table(rows, title='\n=== ALL KERNELS (cpu vs cupy vs torch) ===')

    # CSV
    csv_path = os.path.join(_HERE, '_gpu_bench_results.csv')
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['kernel', 'size', 'cpu_ms', 'cupy_ms', 'torch_ms', 'equiv',
                    'best_speedup', 'section', 'section_share', 'whole_run_speedup', 'note'])
        for r in rows:
            sec = _SECTION_OF.get(r['kernel'].split(':', 1)[0], '')
            sp = _best_speedup(r)
            sh = shares.get(sec)
            w.writerow([r['kernel'], r['size'], f'{r["cpu_ms"]:.3f}' if r['cpu_ms'] else '',
                        f'{r["cupy_ms"]:.3f}' if r['cupy_ms'] else '',
                        f'{r["torch_ms"]:.3f}' if r['torch_ms'] else '',
                        r['equiv'], f'{sp:.2f}' if sp else '',
                        sec, f'{sh:.3f}' if sh else '',
                        f'{_amdahl(sh, sp):.3f}' if (sh and sp) else '', r['note']])

    # Verdicts: largest-size row per candidate, speedup x section-share -> whole-run bound
    print('\n=== VERDICTS (best GPU speedup x wall-share -> whole-run bound) ===')
    verdict_lines = []
    for cand in ('sampling', 'placement', 'picksim'):
        cand_rows = [r for r in rows if r['kernel'].startswith(cand)]
        if not cand_rows:
            continue
        r = cand_rows[-1]                                          # largest size
        sec = _SECTION_OF.get(cand, '')
        sp, sh = _best_speedup(r), shares.get(sec)
        whole = _amdahl(sh, sp) if (sh and sp) else None
        eq = r['equiv']
        eq_s = ('equiv' if eq else ('NOT-equiv' if eq is False else 'n/a'))
        line = (f'  {cand:10} section={sec:6} share={(f"{sh:.0%}" if sh else "?"):>4}  '
                f'best GPU={"%.1fx" % sp if sp else "slower/none":>10}  ({eq_s})  '
                f'-> whole-run {"%.2fx" % whole if whole else "~1x"}')
        print(line)
        verdict_lines.append(line.strip())

    # assessment doc
    docs = os.path.join(_ROOT, 'docs')
    os.makedirs(docs, exist_ok=True)
    with open(os.path.join(docs, 'gpu_assessment.md'), 'w', encoding='utf-8') as f:
        f.write('# GPU acceleration assessment (measured)\n\n')
        f.write('Backends: ' + B.describe_backends() + '\n\n')
        f.write('Fixed-seed CPU-vs-GPU microbenchmarks (`Tests/bench_gpu_*`), weighted by real\n'
                'wall-share from the newest run.log (`Tests/bench_sections.py`).\n\n')
        f.write('## Wall-share (Amdahl)\n')
        for s, v in sorted(shares.items(), key=lambda kv: -kv[1]):
            f.write(f'- {s}: {v:.0%}\n')
        f.write('\n## Verdicts\n')
        for ln in verdict_lines:
            f.write(f'- {ln}\n')
        f.write('\n## Notes / constraints\n')
        f.write('- Sampling (`build`) is the biggest share but the draw is sequential with a '
                'per-step host sync; a faithful GPU port matched CPU exactly but a one-shot '
                'static Gumbel-top-k diverges (drops dynamic partner lift) and is not adoptable '
                'without re-baselining.\n')
        f.write('- Placement greedy select + dynamic affinity stay sequential; only the static '
                'cost matrix is GPU-parallel.\n')
        f.write('- Pick sim is ~5% of wall (low ceiling).\n')
        f.write('- Determinism: any GPU RNG changes the batch stream; adopt only if all arms '
                'use the same deterministic sampler and metrics are re-baselined.\n')
        f.write('- See `Tests/_gpu_bench_results.csv` for the full per-size table.\n')
    print(f'\nWrote {csv_path}\n      {os.path.join(docs, "gpu_assessment.md")}')


if __name__ == '__main__':
    main()
