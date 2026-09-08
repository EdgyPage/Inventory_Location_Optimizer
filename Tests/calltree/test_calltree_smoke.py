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
# coverage/safety are scaled together (2 / 0.4) so stock depletes within a 5-batch run
# while the rp/eq fraction stays production-shaped (~0.2) — safety left at 2 with small
# coverage would degenerate to rp = eq−1 and reorder nearly every batch.
_BUILD = dict(n_skus=300, bins_per_aisle=40, n_pickers=4, seed=42,
              coverage=2.0, safety=0.4)
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


# ── the framework can see the put-away path at all ────────────────────────────────

def test_a_staged_config_actually_exercises_the_held_path():
    """THE gap this closes. Before `--config` existed, no runnable ladder set `put_timing`,
    `put_split`, `put_staging` or `recv_crew`, so `_admit_held`, `_held` and `HeldItems` were
    structurally dead in every rung — a fix to them had zero coverage from any committed
    measurement, and the ladder still printed a confident report.

    Asserted as a FLOW, deliberately. `len(mgr._held)` at the end of this run is 0 and proves
    nothing either way: the backlog drains. That is exactly how a run whose held path fired
    thirteen million times reported `held: 0` and was read as "the path never ran".
    """
    import calltree_growth as cg

    cfg = cg.CONFIGS['split_staging4']
    build, run_kw, _nb = cg._split_kwargs(dict(cfg.overlay, n_skus=150), 42)
    assets = scenarios.build_assets(**build)

    tr = ct.CallTreeTracer(track_c_calls=False)
    tr.start()
    scenarios.run_meso(assets, n_batches=2, seed=42, **run_kw)
    tr.stop()

    tree = tr.tree().to_dict()
    flows = cg._flows(tree, cg._flat_counts(tree))

    assert flows['held_appends'] > 0, (
        f'nothing was ever held under a staging floor of '
        f'{cfg.overlay["put_staging"]} — the configuration does not reach the path it '
        f'exists to exercise. flows={flows}')
    assert flows['refill_passes'] > 0, f'the retry never ran. flows={flows}'
    assert flows['held_retry_touches'] > 0, (
        f'the retry ran but examined nothing, so `_counts_under` is not finding '
        f'PutQueue.admit beneath _admit_held. flows={flows}')

    # ...and the level says nothing, which is the whole point of measuring the flow
    levels = cg._levels(assets.mgr)
    assert levels['held'] == 0, (
        'the backlog did NOT drain here — rewrite this assertion rather than deleting it, '
        'but the flow above is still the load-bearing one')


def _flows_for(cfg_name, n_skus=150, n_batches=2):
    import calltree_growth as cg
    build, run_kw, _nb = cg._split_kwargs(
        dict(cg.CONFIGS[cfg_name].overlay, n_skus=n_skus), 42)
    assets = scenarios.build_assets(**build)
    tr = ct.CallTreeTracer(track_c_calls=False)
    tr.start()
    scenarios.run_meso(assets, n_batches=n_batches, seed=42, **run_kw)
    tr.stop()
    tree = tr.tree().to_dict()
    return cg._flows(tree, cg._flat_counts(tree))


def test_it_is_the_FLOOR_that_creates_a_held_backlog():
    """The control for the test above, and a sharper claim than "the default does nothing".

    `baseline_put` is the same tight warehouse with put-away timed and NO staging floor. It
    must admit real merchandise and hold none of it. If it held something, the staged run's
    flows would not be attributable to the floor; if it admitted nothing, the comparison would
    be between two empty runs.
    """
    unstaged = _flows_for('baseline_put')
    staged   = _flows_for('split_staging4')

    assert unstaged['queue_admissions'] > 0, (
        f'no put-away happened even without a floor, so neither run measures anything: '
        f'{unstaged}')
    assert unstaged['held_appends'] == 0, (
        f'merchandise was held with NO staging floor configured: {unstaged}. Either the floor '
        f'is not what creates the backlog, or a default floor leaked in.')
    assert staged['held_appends'] > 0, f'the floor held nothing: {staged}'


def test_the_default_config_does_not_reach_the_held_path():
    """`--config none` genuinely does not exercise it, and the tool must SAY so rather than
    print an empty report — a reader cannot otherwise tell "not measured" from "measured and
    clean", which is the state this package was in.

    RE-BASELINED after "Field the requirement": `refill_passes` is no longer 0.  The planner
    used to grow every level into leftover capacity, so a default-coverage run at test scale
    barely reordered at all; it now fields exactly the declaration, the shelves are smaller,
    and one `_admit_held` refill pass fires.  That is a correct behavioural consequence, not
    a regression — but the HELD path proper (`HeldItems.append`, the retry touches) is still
    dark under `--config none`, which is the claim this test exists to keep honest, and the
    claim the staged-config test above is the other half of.
    """
    flows = _flows_for('none')
    assert flows['held_appends'] == 0 and flows['held_retry_touches'] == 0, (
        f'the default configuration reached the held path after all: {flows}. If that is '
        f'deliberate, the staged-config test is no longer measuring anything the default '
        f'does not already cover.')


# ── a knee is the shape a single fit hides ────────────────────────────────────────

def test_the_knee_detector_catches_what_the_r2_gate_drops():
    """THE case it was built for, with the real numbers.

    `save_s` on the deep ladder went 784 / 1,014 / 1,497 / 18,343 seconds. A single OLS fit
    gives k=1.42 at r²=0.77, so `MIN_R2 = 0.90` drops it — the largest super-linear jump in the
    artifact reported as nothing. That is backwards for the failure mode that matters: a smooth
    power law fits WELL and gets flagged, while a threshold crossed between two rungs fits badly
    and does not.
    """
    import calltree_growth as cg

    xs = [10_000, 20_000, 40_000, 80_000]
    save_s = [784.15, 1014.46, 1496.54, 18343.23]

    slope, r2 = cg._fit_loglog(xs, save_s)
    assert r2 < cg.MIN_R2, (
        f'the single fit now scores r²={r2:.2f}, so the r² gate would catch this after all '
        f'and the premise of the knee detector has changed')

    k = cg._knee(xs, save_s)
    assert k is not None, f'the knee detector missed the case it exists for (fit was k={slope})'
    assert k['last_step_k'] > 3.0
    assert k['earlier_median_k'] < 1.0
    assert k['at_x'] == 80_000


def test_the_knee_detector_does_not_fire_on_a_clean_curve():
    """NON-VACUITY, and the more important half: a detector that flags everything is a
    detector nobody reads. `reord_s` from the same artifact is linear at every step and must
    stay silent."""
    import calltree_growth as cg

    xs = [10_000, 20_000, 40_000, 80_000]
    assert cg._knee(xs, [606.6, 1150.33, 2238.87, 4407.17]) is None, 'fired on linear reord_s'
    # ...and a genuinely quadratic series is smooth, so it is the FIT's job, not the knee's
    assert cg._knee(xs, [1.0, 4.0, 16.0, 64.0]) is None, 'fired on a clean power law'
    # a series that is flat then jumps IS a knee, however small the numbers
    assert cg._knee(xs, [10.0, 10.5, 11.0, 100.0]) is not None
