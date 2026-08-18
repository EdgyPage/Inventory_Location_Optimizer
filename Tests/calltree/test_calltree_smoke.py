"""
test_calltree_smoke.py — the measurement framework measures what it claims to.

A failure here means the framework itself broke: the tracer stopped seeing threads, the
scenario stopped firing reorders (the silent trap this framework exists to kill), sections
stopped covering the loop, or determinism regressed. No absolute-time asserts anywhere —
CI wall-clock variance is not a defect.

Run: python -m pytest Tests/calltree/test_calltree_smoke.py -q
"""
from __future__ import annotations

import time

import calltree_scenarios as scenarios
import calltree_tracer as ct

# Tiny but real: big enough that every section does work, small enough for seconds.
_BUILD = dict(n_skus=300, bins_per_aisle=40, n_pickers=4, seed=42)
_N_BATCHES = 5


def _find(node: ct.Node, frag: str, out: list) -> list:
    for c in node.children.values():
        if frag in c.name:
            out.append(c)
        _find(c, frag, out)
    return out


def _capture(seed: int, n_skus: int = 300, n_batches: int = _N_BATCHES):
    build = dict(_BUILD, seed=seed, n_skus=n_skus)
    assets = scenarios.build_assets(**build)
    tracer = ct.CallTreeTracer()
    tracer.start()
    result = scenarios.run_meso(assets, n_batches=n_batches, seed=seed, tracer=tracer)
    tracer.stop()
    return tracer.tree(), result


def test_scenario_fires_placement_and_picks():
    # The dead-reorder trap, asserted dead: a scenario where placements == 0 would
    # silently exclude every assignment function from every measurement.
    assets = scenarios.build_assets(**_BUILD)
    t0 = time.perf_counter()
    r = scenarios.run_meso(assets, n_batches=_N_BATCHES, seed=42)
    wall = time.perf_counter() - t0

    assert r.placements > 0, 'zero reorder placements — the assignment fns never ran'
    assert r.reorders > 0
    assert r.picks > 0
    assert r.skipped < _N_BATCHES

    # Sections partition the loop: their sum tracks the wall (glue is small, not zero).
    sec_sum = sum(r.sections.values())
    assert sec_sum > 0.0
    assert abs(sec_sum - wall) / wall < 0.15, \
        f'sections sum {sec_sum:.3f}s vs wall {wall:.3f}s — a phase escaped its timer'


def test_tracer_sees_engine_threads_and_placement():
    root, r = _capture(seed=42)

    # Production engine present, once per non-skipped batch.
    sim_runs = _find(root, 'DeferredPickSimulation.run', [])
    run_calls = sum(n.calls for n in sim_runs if n.kind == 'fn'
                    and n.name.endswith('DeferredPickSimulation.run'))
    assert run_calls >= _N_BATCHES - r.skipped

    # Thread capture — the thing cProfile cannot do: every picker thread's frames.
    thread_fn = _find(root, '_simulate_picker_deferred', [])
    assert sum(n.calls for n in thread_fn) >= (_N_BATCHES - r.skipped) * _BUILD['n_pickers']

    # The reorder section contains an assignment-function frame (placement really traced).
    t_reord = root.children.get('t_reord')
    assert t_reord is not None, 'no t_reord section node'
    assert _find(t_reord, 'Assignment_Functions:', []), \
        'no assignment-function frames under t_reord'

    # Section vocabulary is complete for the phases the meso loop runs.
    section_names = {c.name for c in root.children.values() if c.kind == 'section'}
    assert {'t_reord', 't_sample', 't_task', 't_pre', 't_inv',
            't_sim', 't_extract'} <= section_names


def test_counts_deterministic_and_seed_sensitive():
    # Same seed twice -> identical project-call fingerprints; different seed -> different.
    # (Small sizes: three traced passes must stay in single-digit seconds.)
    root_a, r_a = _capture(seed=7, n_skus=250, n_batches=3)
    root_b, r_b = _capture(seed=7, n_skus=250, n_batches=3)
    root_c, r_c = _capture(seed=8, n_skus=250, n_batches=3)

    assert (r_a.picks, r_a.placements) == (r_b.picks, r_b.placements)
    fp_a = ct.counts_fingerprint(root_a)
    assert fp_a == ct.counts_fingerprint(root_b), 'same-seed capture drifted'
    # Non-vacuity: the fingerprint must actually react to different work.
    assert fp_a != ct.counts_fingerprint(root_c), 'fingerprint blind to a seed change'


def test_section_attribution_covers_the_tree():
    root, _ = _capture(seed=42, n_skus=250, n_batches=3)
    attributed = ct.attribute_sections(root)
    covered = {k for k, v in attributed.items() if v > 0}
    assert {'t_reord', 't_sim', 't_extract'} <= covered
    # Sections' cum should dominate the root's total (little work outside sections).
    total = sum(c.cum_s for c in root.children.values())
    assert total > 0
    assert sum(attributed.values()) / total > 0.90
