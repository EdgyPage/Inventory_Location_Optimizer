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


# -- ranking: a fit needs a real anchor, and k alone is not a priority ------------------

def _ladder(xs, sections):
    """The minimum `fit_report` accepts: rungs carrying x and a sections dict."""
    return {'knob': 'skus', 'config': 'none',
            'rungs': [{'x': x, 'sections': {k: v[i] for k, v in sections.items()},
                       'counts': {}, 'flows': {}, 'flows_per_placement': {}}
                      for i, x in enumerate(xs)]}


def test_a_wall_fit_anchored_on_timer_noise_is_suppressed_not_flagged():
    """THE deep ladder's top offender, with its RAW numbers.

    `t_task` fitted k=3.916 at r2=0.946 and led the offender table of
    `growth__knob-skus_ladder-deep_seed-42__20260820T231003Z_2cdea4386ab7.json`. Its walls
    start at 4.9e-05 s. Fifty microseconds is timer noise, and the exponent it produced
    outranked every true finding in that artifact.

    The values below are the ladder's own, NOT the four-decimal `walls` the report renders --
    those round the first rung to `0.0`, which fits at r2=0.877 and would be dropped by the r2
    gate instead, testing a different thing entirely. Cite the artifact, not its display table.

    The existing `max(ys) < 0.01` gate cannot catch this: it asks whether the section ever got
    BIG, and what poisons the fit is the SMALLEST positive point.

    Suppressed, never dropped -- "too small to fit here" is itself a finding, and the reason
    has to reach the reader or the next session re-derives it.
    """
    import calltree_growth as cg

    xs = [10_000, 20_000, 40_000, 80_000]
    raw = [4.901960784313726e-05, 0.0003921568627450981,
           0.035637254901960784, 0.0927450980392157]
    slope, r2 = cg._fit_loglog(xs, raw)
    assert round(slope, 3) == 3.916 and r2 >= cg.MIN_R2, (
        f'the archived fit no longer reproduces (k={slope:.3f}, r2={r2:.3f}); this test is '
        f'about THAT fit, so a changed fitter needs the numbers re-derived')

    rep = cg.fit_report(_ladder(xs, {'t_task': raw}))

    assert not [o for o in rep['offenders'] if o['name'] == 't_task'], (
        'a fit resting on a 0.4 ms wall is still being reported as an offender')
    sup = [s for s in rep['suppressed'] if s['name'] == 't_task']
    assert sup, 'the suppression was silent - the reader is told nothing'
    assert sup[0]['exponent'] > 3.0, 'the exponent should still be recorded, just not ranked'
    assert 'ms' in sup[0]['why'], 'the reason does not name the anchor that disqualified it'
    assert sup[0]['anchor_s'] < cg.MIN_WALL_S


def test_a_section_with_a_real_anchor_still_flags():
    """NON-VACUITY. The floor must not silence genuine findings - a suppressor that suppresses
    everything is the same defect in the other direction."""
    import calltree_growth as cg

    xs = [10_000, 20_000, 40_000, 80_000]
    rep = cg.fit_report(_ladder(xs, {'t_sim': [1.0, 4.0, 16.0, 64.0]}))   # clean quadratic

    off = [o for o in rep['offenders'] if o['name'] == 't_sim']
    assert off, 'a clean k=2 fit anchored on a whole second was suppressed'
    assert not rep['suppressed'], f'unexpected suppressions: {rep["suppressed"]}'
    assert off[0]['projected'] > off[0]['last'], 'the projection did not carry forward'


def test_offenders_rank_by_cost_class_then_size_not_by_bare_exponent():
    """The project's own lesson, applied to the tool that taught it: "element counts must be
    weighted by COST CLASS before ranking" (docs/design/INBOUND_PERF_FINDINGS.md).

    Two offenders, and the one with the LOWER exponent is the larger problem: 5.4 M calls at
    k=1.45 projects to ~150 M, against 300 k calls at k=1.53 projecting to ~10 M. Sorting by k
    alone puts the small one first, which is how a 5.4 M-call accessor sat at rank 9.
    """
    import calltree_growth as cg

    big = {'kind': 'call-count', 'name': 'big', 'exponent': 1.45,
           'last': 5_453_719, 'projected': cg._project([5_453_719], 1.45)}
    small = {'kind': 'call-count', 'name': 'small', 'exponent': 1.53,
             'last': 376_928, 'projected': cg._project([376_928], 1.53)}

    assert small['exponent'] > big['exponent'], 'premise: the small one has the higher k'
    assert big['projected'] > small['projected'], 'premise: the big one projects larger'

    ranked = cg._severity_sort([small, big])
    assert [o['name'] for o in ranked] == ['big', 'small'], (
        'ranked by bare exponent again - the larger projected cost must come first')


def test_seconds_outrank_calls_because_only_one_of_them_is_a_cost():
    """A section's seconds ARE the cost; a call count is a proxy for one. Ranking across the
    two by a shared number would invent a common unit that does not exist, so the table groups
    by class and sorts by magnitude within each."""
    import calltree_growth as cg

    wall = {'kind': 'section-wall', 'name': 'sec', 'exponent': 1.6, 'projected': 30.0}
    calls = {'kind': 'call-count', 'name': 'fn', 'exponent': 1.6, 'projected': 5.4e7}
    knee = {'kind': 'knee', 'name': 'k', 'exponent': 9.9}

    ranked = cg._severity_sort([knee, calls, wall])
    assert [o['name'] for o in ranked] == ['sec', 'fn', 'k'], (
        'a knee with a huge local k still sorts above priced seconds, or calls outrank '
        'seconds because 5.4e7 > 30 - the two are not the same unit')


# -- the "nothing was measured" line must read what it is about ------------------------

def test_the_all_zero_warning_stays_silent_when_flows_fired():
    """THE defect, with the numbers that exposed it.

    The HEAD ladder printed, two lines apart, on the same rung:

        flows (traced, cumulative): ... held_appends=40,720 held_retry_touches=50,912 ...
        flows: ALL ZERO -- the put-away/receiving path did not execute under
                           cfg=split_staging4. Use --config split_staging4 to exercise it.

    The warning was the `else` of the per-ENTRY-CALL branch -- an inbound-only quantity -- so on
    every non-inbound config it fired regardless of the flows, and told the reader to switch to
    the config they were already running.

    This matters more than a cosmetic wrong line. `Tests/calltree/README.md` instructs the
    reader that "`flows: ALL ZERO` in the per-rung output means *not measured*", and records a
    run whose held path executed millions of times reporting `held: 0` with that zero read as
    "the path never ran". A warning that cries wolf on every rung trains the reader to skip the
    line that exists to stop them trusting a zero.
    """
    import calltree_growth as cg

    real = {'refill_passes': 10_191, 'held_retry_touches': 50_912, 'held_appends': 40_720,
            'queue_admissions': 91_779, 'pool_opens': 14_942}
    assert cg._flows_warning(real, 'split_staging4') is None, (
        'the ALL-ZERO warning fires on a rung that recorded 40,720 held appends')


def test_the_all_zero_warning_still_fires_when_nothing_ran():
    """NON-VACUITY, and the half that must survive: the line exists because a config that
    silently exercises nothing is indistinguishable from a subsystem that costs nothing."""
    import calltree_growth as cg

    for flows in ({}, {'held_appends': 0, 'pool_opens': 0}):
        msg = cg._flows_warning(flows, 'none')
        assert msg and 'NOT MEASURED' in msg, f'silent on {flows!r}'


def test_the_warning_does_not_tell_you_to_use_the_config_you_are_running():
    """The other half of the original bug, and the one that makes the line actively misleading
    rather than merely noisy."""
    import calltree_growth as cg

    on_ss4 = cg._flows_warning({}, 'split_staging4')
    assert on_ss4 is not None
    assert '--config split_staging4' not in on_ss4, (
        'still advising --config split_staging4 while running split_staging4')

    on_none = cg._flows_warning({}, 'none')
    assert '--config split_staging4' in on_none, (
        'the hint is gone entirely -- it is useful on a config that genuinely cannot reach '
        'the path')


# -- the trend: which WAY the local exponents go, which a single fit cannot say ---------

#: The real `AffinityStore.delta_lift_idxs` call counts from the 2026-09-16 HEAD meso
#: artifact, and the `_index_add` counts from the same run. The second is the denominator
#: that makes the first legible: `_reclaim_empty_bins` calls `_index_add` once per reclaimed
#: bin unconditionally and `delta_lift_idxs` only when that bin held the SKU's last unit in
#: the aisle, so the ratio cannot exceed 1.0 and the code guarantees it.
_LADDER_XS      = [500, 1000, 2000, 4000, 8000]
_DELTA_LIFT     = [1925, 7287, 23079, 64176, 157981]
_INDEX_ADD      = [11359, 24025, 45624, 89435, 181441]
#: `_TravelBalancedPool._aisle_best` from the same artifact -- the one offender in the table
#: whose local exponents RISE, and the only one this round left as a live candidate.
_AISLE_BEST     = [5552, 17011, 41479, 125659, 427497]
#: `delta_lift_idxs.<locals>.<genexpr>` -- the row scan inside the call above. It is the
#: converging series that PROJECTS LARGEST of anything in the artifact, which is what makes
#: it the honest partner for the ranking test below.
_DELTA_LIFT_GEN = [22819, 101596, 318159, 796808, 1581454]


def test_the_trend_catches_a_ratio_saturating_toward_its_ceiling():
    """THE case it exists for, with the real numbers.

    `delta_lift_idxs` led every archived `skus` ladder back to August at k=1.56-1.61 with
    r²=0.994 -- a textbook fit, and not a complexity finding. It is a bounded ratio measured
    across the span where it saturates: 0.169 -> 0.871 of a ceiling of exactly 1.0. Extending
    the fit puts that ratio above 1.0 at roughly 13,000 SKUs, and the ladder stops at 8,000.
    """
    import calltree_growth as cg

    slope, r2 = cg._fit_loglog(_LADDER_XS, [float(y) for y in _DELTA_LIFT])
    assert slope >= cg.FLAG_COUNT_EXP and r2 >= cg.MIN_R2, (
        f'premise: this series is still FLAGGED by the fit (k={slope:.2f}, r²={r2:.3f}). '
        f'If it no longer is, the ranking changed and this test should be re-pointed.')

    t = cg._trend(_LADDER_XS, _DELTA_LIFT)
    assert t is not None and t['verdict'] == 'saturating', (
        f'the trend missed the case it exists for: {t}')
    assert t['local'] == [1.92, 1.66, 1.48, 1.3], t['local']
    assert 'ceiling' in t['why'], 'a saturating verdict must say what to check before acting'

    # And the ratio itself, which is what makes the verdict more than a curve-shape guess.
    ratios = [d / i for d, i in zip(_DELTA_LIFT, _INDEX_ADD)]
    assert max(ratios) < 1.0, 'premise: the ratio has not reached its ceiling on this ladder'
    assert cg._trend(_LADDER_XS, ratios)['verdict'] == 'saturating'


def test_the_trend_stays_silent_on_the_series_that_is_actually_growing():
    """NON-VACUITY, and the half that decides whether the verdict is worth reading.

    A detector that calls everything converging is worse than none, because it would have
    retired the one real candidate along with the four false ones. `_aisle_best` is that
    candidate: its local exponents RISE, 1.62 -> 1.77.
    """
    import calltree_growth as cg

    t = cg._trend(_LADDER_XS, _AISLE_BEST)
    assert t is not None and t['verdict'] != 'saturating', (
        f'called the one diverging offender converging: {t}')
    assert t['last'] > t['first'], t['local']
    assert 'why' not in t, 'only a saturating verdict carries the "check the denominator" note'

    # A clean power law is not converging either -- it is exactly what the fit is FOR.
    assert cg._trend(_LADDER_XS, [1.0, 4.0, 16.0, 64.0, 256.0])['verdict'] == 'sustained'
    # ...and a series that flattens outright is the clearest case of all.
    assert cg._trend(_LADDER_XS, [1.0, 4.0, 8.0, 10.0, 10.5])['verdict'] == 'saturating'


def test_the_trend_refuses_to_call_a_direction_from_two_steps():
    """Two local exponents are a difference, not a direction. The deep ladder can legitimately
    run three rungs (`run_deep_ladder` drops rungs above the catalogue), so this is reachable
    in practice rather than a defensive branch."""
    import calltree_growth as cg

    assert cg._trend([500, 1000, 2000], [1.0, 4.0, 16.0]) is None, 'called it from two steps'
    assert cg._trend([500, 1000, 2000, 4000], [1.0, 4.0, 16.0, 64.0]) is not None
    # A zero anywhere makes a log-log step undefined; that is a refusal, not a verdict.
    assert cg._trend(_LADDER_XS, [1.0, 0.0, 16.0, 64.0, 256.0]) is None


def test_a_converging_offender_is_ranked_below_a_diverging_one():
    """End to end through `fit_report`, because the lookup that attaches the trend to an
    offender is where this can silently do nothing -- the verdict is read off the series
    stored in the report, under a key that differs per offender kind.

    THE PAIR IS CHOSEN SO THE TREND IS WHAT DECIDES. `projected` sorts first, so most pairs
    come out in the right order for reasons that have nothing to do with the trend -- an
    earlier draft of this test asserted exactly that and would have passed with the sort key
    reverted. Here the converging series projects 52 M against 15 M, so without the trend it
    wins; the final assertion strips the verdict and shows the order flip back.
    """
    import calltree_growth as cg

    ladder = {'knob': 'skus', 'config': 'none',
              'rungs': [{'x': x, 'sections': {},
                         'counts': {'saturating': _DELTA_LIFT_GEN[i],
                                    'growing': _AISLE_BEST[i]},
                         'flows': {}, 'flows_per_placement': {}}
                        for i, x in enumerate(_LADDER_XS)]}
    report = cg.fit_report(ladder)

    by_name = {o['name']: o for o in report['offenders'] if o['kind'] == 'call-count'}
    assert set(by_name) == {'saturating', 'growing'}, (
        f'premise: both series are still flagged as offenders, got {sorted(by_name)}')
    assert by_name['saturating']['projected'] > by_name['growing']['projected'], (
        'premise: the converging series projects LARGER, so the magnitude key alone would '
        'rank it first -- without that, this test proves nothing about the trend')
    assert by_name['saturating']['trend']['verdict'] == 'saturating'
    assert by_name['growing']['trend']['verdict'] != 'saturating'

    order = [o['name'] for o in report['offenders'] if o['kind'] == 'call-count']
    assert order == ['growing', 'saturating'], (
        f'converging series still outranks the diverging one: {order}')

    # NON-VACUITY: the same two offenders with the verdict removed must rank the other way.
    stripped = [{k: v for k, v in o.items() if k != 'trend'} for o in report['offenders']]
    assert [o['name'] for o in cg._severity_sort(stripped)] == ['saturating', 'growing'], (
        'the trend is not what produced the order above -- `projected` alone already gives '
        'it, so this test would pass with the sort key reverted')


# -- per-arm growth: the sum can be linear while one family pulls away ------------------

#: The 2026-09-16 deep ladder, 136 arms over 10k-80k SKUs. `_DEEP_MAX` is `total_s_max`, whose
#: argmax MIGRATED across the rungs (opt_cluster_map -> uni_cluster_map -> uni_cmin -> uni_cmax,
#: all one placement family); `_DEEP_MEAN` is `total_s_sum / 136` from the same rungs, standing
#: in for the 135 arms that did not diverge.
_DEEP_XS   = [10_000, 20_000, 40_000, 60_000, 80_000]
_DEEP_MAX  = [36.06, 70.95, 144.75, 258.28, 410.86]
_DEEP_MEAN = [16.78, 33.21, 67.56, 107.09, 150.52]


def _deep_ladder(per_arm: dict):
    """A deep-tier ladder carrying only what the per-arm fit reads."""
    rungs = []
    for i, x in enumerate(_DEEP_XS):
        arms = {a: ys[i] for a, ys in per_arm.items()}
        rungs.append({'x': x, 'sections': {}, 'counts': {}, 'flows': {},
                      'flows_per_placement': {}, 'wall_s': 490.0 * (i + 1),
                      'arms': {'arms': len(arms), 'total_s_sum': sum(arms.values()),
                               'phase_model_s': 1.0, 'sections_sum': {}, 'residual_s': 0.0,
                               'residual_frac': 0.0, 'precomp_s_sum': 0.0,
                               'total_s_max': max(arms.values()),
                               'slowest_arm': {'arm': max(arms, key=arms.get),
                                               'total_s': max(arms.values())},
                               'peak_rss_mib_max': 1.0, 'n_bins': 1, 'n_aisles': 1,
                               'per_arm_total_s': arms}})
    return {'knob': 'skus', 'config': 'none', 'rungs': rungs}


def test_a_diverging_arm_is_caught_although_its_fit_is_under_every_threshold():
    """THE case the per-arm series exists for, with the deep ladder's real numbers.

    The diverging arm fits k=1.15 over the whole span — under `FLAG_TIME_EXP` (1.50) and under
    `FLAG_COUNT_EXP` (1.30), so no threshold on the fit would ever report it. Its local
    exponents are 0.98, 1.03, 1.43, 1.61: the first half of the ladder is linear and averages
    the second half away. That is the shape a single fit is structurally unable to show, and
    the arm holding it is the slowest arm at every rung of the ladder.
    """
    import calltree_growth as cg

    report = cg.fit_report(_deep_ladder({'uni_cmax_norsl': _DEEP_MAX,
                                         'uni_rank_labor_norsl': _DEEP_MEAN}))

    fast = report['arm_growth']['uni_cmax_norsl']
    assert fast['exponent'] < cg.FLAG_TIME_EXP and fast['exponent'] < cg.FLAG_COUNT_EXP, (
        f"premise: the fit alone does NOT flag this arm (k={fast['exponent']}). If it now "
        f'does, the thresholds moved and this test is no longer about the trend.')
    assert fast['trend'] == 'accelerating', fast
    assert fast['local'] == [0.98, 1.03, 1.43, 1.61], fast['local']

    flagged = [o['name'] for o in report['offenders'] if o['kind'] == 'arm-total']
    assert 'arm:uni_cmax_norsl' in flagged, (
        f'the diverging arm was not reported at all: {flagged}')


def test_the_other_arms_do_not_all_come_out_diverging():
    """NON-VACUITY. 136 arms means a verdict that fires easily fires 136 times and is read
    zero times. The stand-in for the arms that did NOT diverge rises too — 0.98 to 1.18 — and
    must stay quiet, which is what `FLAG_TREND_DELTA` is for."""
    import calltree_growth as cg

    report = cg.fit_report(_deep_ladder({'uni_cmax_norsl': _DEEP_MAX,
                                         'uni_rank_labor_norsl': _DEEP_MEAN}))

    slow = report['arm_growth']['uni_rank_labor_norsl']
    assert slow['trend'] == 'sustained', slow
    assert slow['local'][-1] > slow['local'][0], (
        'premise: this series rises too — if it were flat the test would prove nothing about '
        'the threshold, only about the sign')
    assert [o['name'] for o in report['offenders'] if o['kind'] == 'arm-total'] \
        == ['arm:uni_cmax_norsl'], 'a quiet arm was reported'


def test_the_sum_stays_linear_while_one_arm_diverges():
    """Why the per-arm series had to exist at all: every section exponent in the deep report
    is built from a SUM over arms, and the sum here is linear at k=1.05 across a span where
    one arm goes from 36 s to 411 s. The divergence is invisible in every aggregate."""
    import calltree_growth as cg

    per_arm = {'uni_cmax_norsl': _DEEP_MAX}
    per_arm.update({f'arm{i:03d}': _DEEP_MEAN for i in range(135)})    # the real 136
    report = cg.fit_report(_deep_ladder(per_arm))

    totals = [r['arms']['total_s_sum'] for r in _deep_ladder(per_arm)['rungs']]
    k_sum, r2_sum = cg._fit_loglog(_DEEP_XS, totals)
    assert k_sum < 1.10 and r2_sum > 0.99, (
        f'premise: the 136-arm sum is linear (k={k_sum:.2f}, r²={r2_sum:.3f})')
    assert cg._trend(_DEEP_XS, totals)['verdict'] == 'sustained', 'the sum hides it'

    assert report['arm_growth']['uni_cmax_norsl']['trend'] == 'accelerating'
    assert [o['name'] for o in report['offenders'] if o['kind'] == 'arm-total'] \
        == ['arm:uni_cmax_norsl'], 'the one diverging arm in 136 was not isolated'


def test_an_arm_missing_from_a_rung_is_skipped_not_zero_filled():
    """A dead worker produces no runtime row. Zero-filling it would read as an arm that got
    faster, which is the direction that hides a problem — and `pool run swallows dead arms`
    records that a whole arm can vanish while the run still exits 0."""
    import calltree_growth as cg

    ladder = _deep_ladder({'uni_cmax_norsl': _DEEP_MAX, 'uni_rank_labor_norsl': _DEEP_MEAN})
    del ladder['rungs'][2]['arms']['per_arm_total_s']['uni_rank_labor_norsl']

    report = cg.fit_report(ladder)
    assert 'uni_rank_labor_norsl' not in report['arm_growth'], (
        'an arm absent from a rung was fitted anyway — on four points misaligned with five xs')
    assert 'uni_cmax_norsl' in report['arm_growth'], 'the complete arm was dropped too'


def test_a_falling_exponent_that_settles_at_two_is_not_saturating():
    """THE BUG THIS VERDICT EXISTS FOR, found by running the instrument on a real cell.

    `cmin`'s `_build_aisle_score_fn.<locals>.assign.<locals>.score_of` is a clean quadratic:
    34,237 → 9,195,611 calls, local exponents 2.41, 1.64, **2.00, 2.02**. The first rule here
    called "saturating" on direction alone (`last <= first - FLAG_TREND_DELTA`), so a series
    that starts super-quadratic and settles at exactly 2.0 was labelled "CONVERGING, the fitted
    k is a transient" — and, worse, `_severity_sort` demoted it to the bottom of its cost class.
    A 9.2M-call quadratic was being pushed below everything by the guard meant to surface it.

    Falling is not the same as heading to linear. A series only earns "saturating" when its last
    local exponent is also below the flag threshold; one that falls and then settles high is
    "settling" — the single fit overstates the early rungs, and the settled value IS the class.
    """
    import calltree_growth as cg

    quadratic = [34_237, 181_498, 566_476, 2_263_535, 9_195_611]

    t = cg._trend(_LADDER_XS, quadratic)
    assert t['local'] == [2.41, 1.64, 2.0, 2.02], t['local']
    assert t['first'] - t['last'] >= cg.FLAG_TREND_DELTA, (
        'premise: this series IS falling, so the old rule really did call it saturating')
    assert t['verdict'] == 'settling', t
    assert t['last'] > cg.FLAG_COUNT_EXP, 'premise: it settles ABOVE the flag threshold'
    assert 'settled' in t['why'] and 'LAST local exponent' in t['why']


def test_only_a_series_heading_to_linear_is_demoted_in_the_ranking():
    """The consequence, end to end.

    THE PAIR MUST OPPOSE THE MAGNITUDE, or this proves nothing. `_severity_sort` keys on
    `projected` before the trend, so a big quadratic beats a small saturating series under
    BOTH rules and a test built on that pair passes with the fix reverted — which is exactly
    what the first draft of this test did, for the second time in one session.

    So: the quadratic here is the SMALL one (cmin's `delta_lift_idxs`, projecting 9.2M) and
    the series heading to linear is the BIG one (`delta_lift_idxs`'s genexpr, projecting
    52M). Under the old rule both counted as "saturating", the tie fell to magnitude, and the
    saturating series won. Only the split verdict puts the quadratic first.
    """
    import calltree_growth as cg

    small_quadratic = [315, 1_855, 8_767, 31_482, 86_170]
    ladder = {'knob': 'skus', 'config': 'cmin',
              'rungs': [{'x': x, 'sections': {},
                         'counts': {'settled_quadratic': small_quadratic[i],
                                    'heading_to_linear': _DELTA_LIFT_GEN[i]},
                         'flows': {}, 'flows_per_placement': {}}
                        for i, x in enumerate(_LADDER_XS)]}
    report = cg.fit_report(ladder)

    by_name = {o['name']: o for o in report['offenders'] if o['kind'] == 'call-count'}
    assert by_name['settled_quadratic']['trend']['verdict'] == 'settling'
    assert by_name['heading_to_linear']['trend']['verdict'] == 'saturating'
    assert by_name['heading_to_linear']['projected'] > by_name['settled_quadratic']['projected'], (
        'premise: the series heading to linear projects LARGER, so magnitude alone ranks it '
        'first — without that, this test says nothing about the verdict')

    order = [o['name'] for o in report['offenders'] if o['kind'] == 'call-count']
    assert order == ['settled_quadratic', 'heading_to_linear'], (
        f'the settled quadratic was demoted below a series heading to linear: {order}')

    # NON-VACUITY: collapse `settling` back into `saturating` — the rule this replaced — and
    # the order must flip back.
    collapsed = [dict(o, trend={'verdict': 'saturating'})
                 if (o.get('trend') or {}).get('verdict') in ('saturating', 'settling') else o
                 for o in report['offenders']]
    assert [o['name'] for o in cg._severity_sort(collapsed)] \
        == ['heading_to_linear', 'settled_quadratic'], (
        'the split verdict is not what produced the order above — this test would pass with '
        'the fix reverted')


def test_the_three_falling_verdicts_are_told_apart_on_real_series():
    """All three shapes, from artifacts on disk, so the boundary is pinned to measurements
    rather than to invented curves."""
    import calltree_growth as cg

    # Refuted this round: a bounded ratio saturating against a ceiling the code guarantees.
    assert cg._trend(_LADDER_XS, _DELTA_LIFT)['verdict'] == 'saturating'
    assert cg._trend(_LADDER_XS, _DELTA_LIFT_GEN)['verdict'] == 'saturating'
    # Convicted this round: cmin's lift scan, quadratic and settled.
    assert cg._trend(_LADDER_XS, [29_483, 160_693, 466_621, 1_854_211,
                                  7_997_764])['verdict'] == 'settling'
    # Convicted this round: cluster_map's tie-break, still steepening at the last rung.
    assert cg._trend(_LADDER_XS, [1_795, 4_542, 24_219, 166_963,
                                  1_174_077])['verdict'] == 'accelerating'
    # Honest "not yet known": falling steadily but still above the threshold at the last step.
    assert cg._trend(_LADDER_XS, [315, 1_855, 8_767, 31_482, 86_170])['verdict'] == 'settling'


# -- the save confound, and the row collapse underneath it -----------------------------

#: Real per-arm totals from the 2026-09-16 deep ladder (5 rungs, 10k -> 80k, 136 rows each),
#: summed over the four (cell, pair, config, channel) rows every arm name owns.
_R4_TOTAL = {
    'uni_cluster_map_norsl': [114.77, 240.93, 477.48, 754.19, 1150.96],
    'uni_cmin_norsl':        [72.29, 153.47, 347.18, 624.08, 1062.17],
    'uni_tmin_norsl':        [61.24, 124.03, 239.84, 346.82, 641.59],
}
#: The same arms with the DB-save section removed.
_R4_EX_SAVE = {
    'uni_cluster_map_norsl': [89.61, 174.67, 362.71, 575.79, 770.44],
    'uni_cmin_norsl':        [47.22, 88.89, 226.82, 433.37, 663.05],
    'uni_tmin_norsl':        [35.57, 59.04, 114.72, 172.54, 231.26],
}


def test_sum_by_arm_sums_the_rows_instead_of_keeping_the_last():
    """THE DEFECT THIS REPLACED, and it cost a whole deep ladder's per-arm reading.

    `runtime_metrics` is unique on (cell, pair, config, channel, arm), so a 136-row run holds
    34 distinct arm NAMES — four rows each. The first version built the per-arm map with a dict
    comprehension keyed on the name, which kept whichever row iterated last and discarded three
    quarters of the run in silence. The only visible symptom was a printed count of 34 sitting
    next to a rollup that said 136, and nothing compared the two.
    """
    import calltree_growth as cg

    rows = [
        {'arm': 'a', 'channel': 'store', 'config': 'x', 'total_s': 10.0},
        {'arm': 'a', 'channel': 'fulfillment', 'config': 'x', 'total_s': 20.0},
        {'arm': 'a', 'channel': 'store', 'config': 'y', 'total_s': 30.0},
        {'arm': 'b', 'channel': 'store', 'config': 'x', 'total_s': 5.0},
    ]
    got = cg._sum_by_arm(rows, lambda r: float(r['total_s']))
    assert got == {'a': 60.0, 'b': 5.0}, (
        f'{got} — an arm total must be the SUM over its rows; keeping the last gives '
        f"{{'a': 30.0, 'b': 5.0}}, which is what the defect produced")


def test_a_shared_save_knee_does_not_flag_every_arm():
    """THE CONFOUND, with the ladder's real numbers.

    On `total_s` alone, 33 of 34 arms read as accelerating and the median last local exponent
    was 1.68. `save_s` was 35.9% -> 48.6% of total with a knee at the top rung (local k 1.27,
    1.06, 0.97, **2.30**), so one shared I/O step lifted nearly every arm at the same rung. With
    save removed the median last local exponent falls to 1.09 and only the co-demand pair
    survives — which independently corroborates the meso cell's conviction of `score_of`.

    `uni_tmin_norsl` is the control: it looks accelerating on `total_s` (last local k 2.14, the
    largest in the run) and is flatly linear once save is removed.
    """
    import calltree_growth as cg

    xs = [10_000, 20_000, 40_000, 60_000, 80_000]

    # Premise: on total_s the control looks like the worst offender in the run.
    t_tot = cg._trend(xs, _R4_TOTAL['uni_tmin_norsl'])
    assert t_tot['verdict'] == 'accelerating' and t_tot['local'][-1] > 2.0, t_tot

    # ...and with save removed it is linear, while the co-demand arm stays superlinear.
    for arm, want_linear in (('uni_tmin_norsl', True),
                             ('uni_cluster_map_norsl', True),
                             ('uni_cmin_norsl', False)):
        k, r2 = cg._fit_loglog(xs, _R4_EX_SAVE[arm])
        if want_linear:
            assert k < cg.FLAG_ARM_EXP, f'{arm} ex-save fits k={k:.2f}, expected linear'
        else:
            assert k >= cg.FLAG_ARM_EXP, (
                f'{arm} ex-save fits k={k:.2f}, expected superlinear — this is the arm the '
                f'meso cell convicted at k=1.98, and the two instruments must agree')


def test_the_ex_save_series_gates_the_offender_list():
    """End to end: an arm whose `total_s` accelerates only because of the save knee must not be
    reported, and one whose placement cost really grows must be."""
    import calltree_growth as cg

    xs = [10_000, 20_000, 40_000, 60_000, 80_000]
    rungs = []
    for i, x in enumerate(xs):
        tot = {a: v[i] for a, v in _R4_TOTAL.items()}
        ex = {a: v[i] for a, v in _R4_EX_SAVE.items()}
        rungs.append({'x': x, 'sections': {}, 'counts': {}, 'flows': {},
                      'flows_per_placement': {}, 'wall_s': 400.0 * (i + 1),
                      'arms': {'arms': len(tot), 'total_s_sum': sum(tot.values()),
                               'phase_model_s': 1.0, 'sections_sum': {}, 'residual_s': 0.0,
                               'residual_frac': 0.0, 'precomp_s_sum': 0.0,
                               'total_s_max': max(tot.values()),
                               'slowest_arm': {'arm': max(tot, key=tot.get),
                                               'total_s': max(tot.values())},
                               'peak_rss_mib_max': 1.0, 'n_bins': 1, 'n_aisles': 1,
                               'per_arm_total_s': tot, 'per_arm_ex_save_s': ex}})
    report = cg.fit_report({'knob': 'skus', 'config': 'none', 'rungs': rungs})

    flagged = {o['name'] for o in report['offenders'] if o['kind'] == 'arm-total'}
    assert 'arm:uni_cmin_norsl' in flagged, (
        f'the genuinely superlinear arm was not reported: {sorted(flagged)}')
    assert 'arm:uni_tmin_norsl' not in flagged, (
        f'a linear arm was reported on the strength of the shared save knee: {sorted(flagged)}')

    # NON-VACUITY: without the ex-save gate, the control WOULD be flagged.
    t = cg._trend(xs, _R4_TOTAL['uni_tmin_norsl'])
    assert t['verdict'] == 'accelerating', (
        'the control no longer accelerates on total_s, so this test no longer proves the gate '
        'is what excluded it')
