# gpu — validated, and intentionally dormant

**This package has no consumer, and that is a decision — not neglect.** It is exercised only by
`Tests/gpu/` (correctness tests plus benches), which the routine suite skips with `-k "not gpu"`.

## Why it is not wired in

The obvious target was placement. That work is **already reduced away**: the expensive candidate
scan was replaced by `_PrefPool` (O(log B) closest-to-target with union-find alive chains) and a
hoisted affinity CSR row, so the remaining hot loop is a cheap greedy that stays on the CPU.
Offloading it would add transfer and synchronisation cost to something that is no longer the
bottleneck.

The measurements behind that conclusion are written up in `docs/gpu_assessment.md` and
`docs/gpu_auction_assessment.md`. Both are published pages listed in `mkdocs.yml`, so they survive
independently of this code.

| Module | Owns |
|---|---|
| `gpu_auction.py` | auction-based assignment solver (numpy + torch paths) |
| `gpu_broker.py` | a governor that serialises GPU access across processes |
| `gpu_client.py` | the client shim workers would talk to |

## Before you wire this in

Re-benchmark first. The assessment docs measured a *previous* placement implementation; the
comparison they make no longer describes the code. Reviving this without new numbers would be
optimising against a bottleneck that has already been removed.

## If you are deleting things

This is one unit. Deleting `Tests/gpu/` alone would strand the source at zero references *and*
remove the only importer of `Tests/bench/bench_sections.py`.
