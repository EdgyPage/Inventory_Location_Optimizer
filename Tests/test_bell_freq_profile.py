"""test_bell_freq_profile.py

Locks in the opt-in ``--freq-profile bell`` demand-skew option added to
``Warehouse/generation/generate_mixed_profile.py``.  The bell profile swaps ONLY the
per-category pick-frequency specs (uniform -> normal for store, a 3-normal mixture for
fulfillment); every other physical attribute of the creation plan is untouched, and the
default ``uniform`` profile stays byte-for-byte the historical baseline.

Invariants pinned here:
  * ``BELL_CREATION_PLAN`` is ``CREATION_PLAN`` in every dataclass field EXCEPT ``freq_spec``,
    and each bell family's freq_spec is exactly ``BELL_STORE_FREQ[category]``.
  * ``BELL_STORE_FREQ`` / ``BELL_FF_FREQ`` carry the exact means/std/probs of the spec.
  * A full inventory built from the bell plan samples frequencies that (a) respect the
    (1e-6, 1.0] clamp and (b) track their assigned per-category means, preserving the
    furniture < seasonal < chemical < food popularity ordering.
  * ``_build_plan`` takes the bell branch only for ``freq_profile == 'bell'`` — with
    ``uniform`` the store/fulfillment freq_specs are the unchanged baseline (non-vacuity guard).

Determinism: ``build_inventory_from_plan`` is seeded by an explicit ``seed`` (self-contained
``random.Random`` internally), so a fixed seed reproduces identical frequency sequences and a
different seed diverges.

Run:  python -m pytest Tests/test_bell_freq_profile.py -q
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import fields, replace

import pytest

from Warehouse.Order import Order
from Warehouse.generation.generate_inventory import build_inventory_from_plan
from Warehouse.generation.generate_mixed_profile import (
    BELL_CREATION_PLAN,
    BELL_FF_FREQ,
    BELL_STORE_FREQ,
    CREATION_PLAN,
    _build_plan,
)

# Exact per-category store targets (mean, std) the bell profile promises.
_EXPECTED_STORE = {
    'food':       (0.80, 0.10),
    'chemical':   (0.50, 0.10),
    'clothing':   (0.35, 0.05),
    'electronic': (0.35, 0.05),
    'seasonal':   (0.15, 0.05),
    'furniture':  (0.05, 0.015),
}
# Exact fulfillment mixture components: mean -> std, each equal-weight (prob 1/3).
_EXPECTED_FF = {0.15: 0.05, 0.25: 0.05, 0.55: 0.10}

_SEED     = 20260708
_BIG_SKUS = 8000       # ~1.3k SKUs/category → stable sample means under a generous tolerance


# ── builders ──────────────────────────────────────────────────────────────────

def _freq_sequence(num_skus: int, seed: int) -> list:
    """Ordered relative_frequency of every SKU built from BELL_CREATION_PLAN.

    Resets Order.next_sku first (build assigns explicit skus, so this only tidies shared
    class state) — the sequence is a pure function of (plan, num_skus, seed)."""
    Order.next_sku = 1
    inv = build_inventory_from_plan(num_skus=num_skus, plan=BELL_CREATION_PLAN, seed=seed)
    return [o.demand.relative_frequency for o in inv.orders]


def _freqs_by_category(num_skus: int, seed: int) -> dict:
    """category -> list of sampled (clamped) relative frequencies, from the bell plan."""
    Order.next_sku = 1
    inv = build_inventory_from_plan(num_skus=num_skus, plan=BELL_CREATION_PLAN, seed=seed)
    out: dict = {}
    for o in inv.orders:
        out.setdefault(o.storage_handle_config.category, []).append(o.demand.relative_frequency)
    return out


def _mean(xs) -> float:
    return sum(xs) / len(xs)


def _plan_args(freq_profile: str, fulfillment_fraction: float) -> argparse.Namespace:
    """A minimal args namespace exposing exactly the attributes _build_plan reads."""
    return argparse.Namespace(
        freq_profile=freq_profile,
        fulfillment_fraction=fulfillment_fraction,
        ff_cube_fraction=0.3,
        ff_weight_spec=None,
        ff_weight_min=1.0,
        ff_weight_mode=2.5,
        ff_weight_max=10.0,
        ff_dim_range=[3, 16],
        ff_cube_sizes=[4, 6, 8],
    )


# ── 1. structural: only freq_spec differs ───────────────────────────────────────

def test_bell_plan_differs_from_baseline_only_in_freq_spec() -> None:
    """BELL_CREATION_PLAN mirrors CREATION_PLAN family-for-family, changing ONLY freq_spec
    (to the per-category bell normal); no dimension/weight/handling/share/qty drift."""
    assert len(BELL_CREATION_PLAN) == len(CREATION_PLAN) == 6, (
        f'plan length changed: bell={len(BELL_CREATION_PLAN)} base={len(CREATION_PLAN)}')

    for base, bell in zip(CREATION_PLAN, BELL_CREATION_PLAN):
        assert bell.category == base.category, (
            f'family order/category drifted: base={base.category} bell={bell.category}')
        # the bell family carries exactly the per-category bell normal
        assert bell.freq_spec == BELL_STORE_FREQ[base.category], (
            f'{base.category}: freq_spec {bell.freq_spec} != {BELL_STORE_FREQ[base.category]}')
        # freq_spec genuinely changed (non-vacuity: bell is not silently the baseline)
        assert bell.freq_spec != base.freq_spec, (
            f'{base.category}: bell freq_spec equals the uniform baseline {base.freq_spec}')
        # every OTHER dataclass field is identical
        restored = replace(bell, freq_spec=base.freq_spec)
        assert restored == base, (
            f'{base.category}: fields beyond freq_spec differ. '
            f'changed={[f.name for f in fields(base) if getattr(restored, f.name) != getattr(base, f.name)]}')


# ── 2. spec correctness ─────────────────────────────────────────────────────────

def test_bell_store_freq_specs_have_exact_normals() -> None:
    """Each store category's BELL_STORE_FREQ entry is a normal with the promised mean/std."""
    assert set(BELL_STORE_FREQ) == set(_EXPECTED_STORE), (
        f'store categories changed: {set(BELL_STORE_FREQ)} vs {set(_EXPECTED_STORE)}')
    for cat, (mean, std) in _EXPECTED_STORE.items():
        spec = BELL_STORE_FREQ[cat]
        assert spec.get('dist') == 'normal', f'{cat}: dist {spec.get("dist")!r} != normal'
        assert abs(spec['mean'] - mean) < 1e-9, f'{cat}: mean {spec["mean"]} != {mean}'
        assert abs(spec['std'] - std) < 1e-9, f'{cat}: std {spec["std"]} != {std}'


def test_bell_ff_freq_is_equal_weight_three_normal_mixture() -> None:
    """BELL_FF_FREQ is a 3-component mixture: probs sum to 1.0, means {0.15,0.25,0.55}."""
    assert BELL_FF_FREQ.get('dist') == 'mixture', f'ff dist {BELL_FF_FREQ.get("dist")!r} != mixture'
    comps = BELL_FF_FREQ['components']
    assert len(comps) == 3, f'expected 3 components, got {len(comps)}'

    probs = [c['prob'] for c in comps]
    assert abs(sum(probs) - 1.0) < 1e-9, f'component probs {probs} do not sum to 1.0'
    for c in comps:
        assert abs(c['prob'] - 1 / 3) < 1e-9, f'component prob {c["prob"]} != 1/3 (not equal-weight)'
        assert c['spec'].get('dist') == 'normal', f'component spec {c["spec"]} is not normal'

    for mean, std in _EXPECTED_FF.items():
        match = [c for c in comps if abs(c['spec']['mean'] - mean) < 1e-9]
        assert len(match) == 1, f'expected exactly one component with mean {mean}, found {len(match)}'
        assert abs(match[0]['spec']['std'] - std) < 1e-9, (
            f'mean {mean}: std {match[0]["spec"]["std"]} != {std}')


# ── 3. end-to-end sampled frequencies ───────────────────────────────────────────

def test_bell_sampled_frequencies_respect_clamp() -> None:
    """Every sampled frequency lands within the (1e-6, 1.0] clamp (boundaries allowed)."""
    by_cat = _freqs_by_category(_BIG_SKUS, _SEED)
    assert set(by_cat) == set(_EXPECTED_STORE), (
        f'built categories {set(by_cat)} != plan categories {set(_EXPECTED_STORE)}')
    for cat, freqs in by_cat.items():
        lo, hi = min(freqs), max(freqs)
        assert lo >= 1e-6, f'{cat}: min frequency {lo} below clamp floor 1e-6'
        assert hi <= 1.0, f'{cat}: max frequency {hi} above clamp ceiling 1.0'


def test_bell_sampled_means_track_targets_and_preserve_ordering() -> None:
    """Per-category sample means track their assigned bell means (abs diff < 0.03), and the
    furniture < seasonal < chemical < food popularity ordering holds — proving the bell
    normals (not the uniform baseline) actually drove the draws."""
    by_cat = _freqs_by_category(_BIG_SKUS, _SEED)
    means  = {cat: _mean(freqs) for cat, freqs in by_cat.items()}

    for cat, (target, _std) in _EXPECTED_STORE.items():
        assert abs(means[cat] - target) < 0.03, (
            f'{cat}: sample mean {means[cat]:.4f} strays > 0.03 from target {target}')

    ordered = ['furniture', 'seasonal', 'chemical', 'food']
    for lo_cat, hi_cat in zip(ordered, ordered[1:]):
        assert means[lo_cat] < means[hi_cat], (
            f'popularity ordering broken: mean({lo_cat})={means[lo_cat]:.4f} '
            f'>= mean({hi_cat})={means[hi_cat]:.4f}')


def test_bell_build_is_reproducible_and_seed_sensitive() -> None:
    """Same seed → byte-identical frequency sequence; a different seed diverges."""
    a = _freq_sequence(num_skus=1200, seed=_SEED)
    b = _freq_sequence(num_skus=1200, seed=_SEED)
    assert a == b, 'identical seed produced different bell frequency sequences'

    c = _freq_sequence(num_skus=1200, seed=_SEED + 1)
    assert a != c, 'distinct seeds produced identical bell frequency sequences (rng not wired)'


# ── 4. _build_plan branch selection ─────────────────────────────────────────────

def test_build_plan_bell_branch_sets_bell_specs() -> None:
    """freq_profile='bell' with fulfillment → store families carry the per-category bell
    normals and fulfillment families carry the BELL_FF_FREQ mixture."""
    plan  = _build_plan(_plan_args('bell', fulfillment_fraction=0.2))
    store = [f for f in plan if f.handling_override is None]
    ff    = [f for f in plan if f.handling_override == 'fulfillment']

    assert len(store) == 6, f'expected 6 store families, got {len(store)}'
    assert ff, 'fulfillment fraction 0.2 must yield at least one fulfillment family'

    for fam in store:
        assert fam.freq_spec == BELL_STORE_FREQ[fam.category], (
            f'{fam.category}: store freq_spec {fam.freq_spec} != {BELL_STORE_FREQ[fam.category]}')
    for fam in ff:
        assert fam.freq_spec == BELL_FF_FREQ, (
            f'fulfillment freq_spec {fam.freq_spec} != BELL_FF_FREQ {BELL_FF_FREQ}')


def test_build_plan_uniform_branch_keeps_baseline() -> None:
    """freq_profile='uniform' does NOT take the bell branch: store freq_specs match the
    baseline CREATION_PLAN and fulfillment stays off the bell mixture (non-vacuity guard)."""
    baseline = {fam.category: fam.freq_spec for fam in CREATION_PLAN}
    plan     = _build_plan(_plan_args('uniform', fulfillment_fraction=0.2))
    store    = [f for f in plan if f.handling_override is None]
    ff       = [f for f in plan if f.handling_override == 'fulfillment']

    assert len(store) == 6, f'expected 6 store families, got {len(store)}'
    assert ff, 'fulfillment fraction 0.2 must yield at least one fulfillment family'

    for fam in store:
        assert fam.freq_spec == baseline[fam.category], (
            f'{fam.category}: uniform freq_spec {fam.freq_spec} != baseline {baseline[fam.category]}')
        assert fam.freq_spec != BELL_STORE_FREQ[fam.category], (
            f'{fam.category}: uniform branch leaked the bell normal {fam.freq_spec}')
    for fam in ff:
        assert fam.freq_spec != BELL_FF_FREQ, (
            f'fulfillment freq_spec {fam.freq_spec} unexpectedly equals BELL_FF_FREQ')


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
