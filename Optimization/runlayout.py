"""runlayout.py — single owner of the on-disk run-tree layout.

Two trees, one vocabulary:

  * profiles tree (generation output):   <profiles>/<run>/<profile>/{inventory,affinity}/*.db
  * comparison tree (simulation output): <base>/<cell>/<pair>/<config>[/<channel>]/{sim_*.db,
                                          sim_meta.json, series.json, ...}

Every run is a CELL matrix: even a plain run is one cell (``k1_off``).  A cell dir is
itself a self-contained comparison root (``<cell>/<pair>/<config>[/<channel>]/…``), so the
per-cell walkers below take a *cell dir* (or a legacy flat base) and are unchanged; only the
top-level ``cells()`` iterator is cell-aware.  ``iter_channel_runs`` / ``iter_sim_dbs`` therefore
run PER CELL — pass a cell dir, or loop ``cells(base_dir)`` to span a whole run.

Every walker that used to re-encode these shapes (run_simulation's pair discovery +
blank-arm scan, run_analysis's config/aggregate walks, run_channel_rollup's series
walk, Visualization/db_reader's run discovery) now goes through here, so a layout
change is a one-file edit.  All listings are ``sorted(os.listdir(...))`` —
deterministic order, matching the original sites.

A *channel run* is the leaf directory an analyzed run lives in: ``<config>/`` itself
for store-only runs, or ``<config>/<channel>/`` per channel on mixed-catalog runs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator


# ── profiles tree (generation output) ───────────────────────────────────────────

def _profile_pairs(run_path: str, run_name: str) -> list[tuple[str, str, str]]:
    """(label, inventory_db, affinity_db) for every valid profile under one run dir."""
    pairs: list[tuple[str, str, str]] = []
    for profile_name in sorted(os.listdir(run_path)):
        profile_path = os.path.join(run_path, profile_name)
        if not os.path.isdir(profile_path):
            continue
        inv_db = os.path.join(profile_path, 'inventory', 'inventory.db')
        aff_db = os.path.join(profile_path, 'affinity', 'affinity.db')
        if os.path.exists(inv_db) and os.path.exists(aff_db):
            pairs.append((f'{run_name}__{profile_name}', inv_db, aff_db))
    return pairs


def discover_db_pairs(profiles_dir: str) -> list[tuple[str, str, str]]:
    """Scan profiles_dir and return (label, inventory_db, affinity_db) for every valid pair."""
    pairs: list[tuple[str, str, str]] = []
    if not os.path.isdir(profiles_dir):
        return pairs
    for run_name in sorted(os.listdir(profiles_dir)):
        run_path = os.path.join(profiles_dir, run_name)
        if not os.path.isdir(run_path):
            continue
        pairs.extend(_profile_pairs(run_path, run_name))
    return pairs


def find_latest_db_pairs(profiles_dir: str) -> list[tuple[str, str, str]]:
    """Return DB pairs from the most recently generated profile run only.

    Profile run directories are named profile_YYYYMMDD_HHMMSS (or the legacy
    batch_YYYYMMDD_HHMMSS), so the last entry when sorted lexicographically is
    always the newest.  Walks backwards until a run with valid pairs is found.
    """
    if not os.path.isdir(profiles_dir):
        return []
    run_names = sorted([
        d for d in os.listdir(profiles_dir)
        if os.path.isdir(os.path.join(profiles_dir, d))
    ])
    for run_name in reversed(run_names):
        pairs = _profile_pairs(os.path.join(profiles_dir, run_name), run_name)
        if pairs:
            return pairs
    return []


# ── comparison tree (simulation output) ─────────────────────────────────────────

def cells(base_dir: str) -> Iterator[tuple[str, str]]:
    """Yield (cell_name, cell_dir) for every cell under a run root.

    Every run is a cell matrix (a plain run is the single cell ``k1_off``).  When the run has a
    ``run_layout.json`` descriptor, its cells are yielded IN DESCRIPTOR ORDER with NO disk probe —
    so a partial/crashed cell (dir exists, no ``sim_*.db`` yet) is still yielded and analyzes on
    resume.  When the descriptor is ABSENT (legacy runs), fall back to walking the tree: the run
    root's immediate non-``_`` children that contain a ``<pair>/<config>/sim_*.db`` subtree are the
    cells; a legacy FLAT run (no cell level — DBs directly under ``<base>/<pair>/<config>``) is
    yielded as one implicit cell named after the base dir.
    """
    if not os.path.isdir(base_dir):
        return
    from Optimization.sim_manifest import read_run_layout
    layout = read_run_layout(base_dir)
    if layout and layout.get('cells'):
        for cell in layout['cells']:
            yield cell['name'], os.path.join(base_dir, cell['name'])
        return
    # ── legacy fallback: no descriptor → infer the cells from the on-disk tree ──
    subs = [d for d in sorted(os.listdir(base_dir))
            if os.path.isdir(os.path.join(base_dir, d)) and not d.startswith('_')]
    found = False
    for name in subs:
        cell_dir = os.path.join(base_dir, name)
        if next(iter_sim_dbs(cell_dir), None) is not None:   # has a <pair>/<config>/sim_*.db subtree
            found = True
            yield name, cell_dir
    if not found and next(iter_sim_dbs(base_dir), None) is not None:
        yield os.path.basename(base_dir.rstrip('/\\')), base_dir   # legacy flat = one implicit cell


@dataclass(frozen=True)
class ChannelRun:
    """One analyzed run directory: <base>/<pair>/<config>[/<channel>]."""
    pair: str
    config: str
    channel: str | None      # None = store-only layout (no channel subdir)
    path: str

    @property
    def group_key(self) -> str:
        """Aggregate grouping key: config, or config/channel on mixed runs."""
        return self.config if self.channel is None else f'{self.config}/{self.channel}'


def iter_channel_runs(base_dir: str, marker: str = 'sim_meta.json') -> Iterator[ChannelRun]:
    """Yield every channel-run dir under base_dir that contains *marker*.

    Store-only runs put the marker directly at <config>/; mixed-catalog runs put one
    per channel at <config>/<channel>/.  Top-level entries starting with '_'
    (e.g. _aggregate/) are skipped.  Order: sorted pair, then config, then channel.
    """
    if not os.path.isdir(base_dir):
        return
    for pair in sorted(os.listdir(base_dir)):
        pair_dir = os.path.join(base_dir, pair)
        if not os.path.isdir(pair_dir) or pair.startswith('_'):
            continue
        for config in sorted(os.listdir(pair_dir)):
            cfg_dir = os.path.join(pair_dir, config)
            if not os.path.isdir(cfg_dir):
                continue
            if os.path.exists(os.path.join(cfg_dir, marker)):
                yield ChannelRun(pair, config, None, cfg_dir)
                continue
            for sub in sorted(os.listdir(cfg_dir)):
                sub_dir = os.path.join(cfg_dir, sub)
                if os.path.isdir(sub_dir) and os.path.exists(os.path.join(sub_dir, marker)):
                    yield ChannelRun(pair, config, sub, sub_dir)


def _sim_dbs_in(run: ChannelRun) -> Iterator[tuple[ChannelRun, str]]:
    for fn in sorted(os.listdir(run.path)):
        if fn.startswith('sim_') and fn.endswith('.db') and not fn.endswith('.keyframes.db'):
            yield run, os.path.join(run.path, fn)


def iter_sim_dbs(base_dir: str) -> Iterator[tuple[ChannelRun, str]]:
    """Yield (ChannelRun, sim_db_path) for every per-strategy result DB under base_dir.

    Structural walk over <pair>/<config>[/<channel>]/sim_<strategy>.db — keyframe DBs
    excluded.  Channel subdirs are found even when sim_meta.json was never finalized
    (crashed runs), so blank-arm scans see everything.
    """
    if not os.path.isdir(base_dir):
        return
    for pair in sorted(os.listdir(base_dir)):
        pair_dir = os.path.join(base_dir, pair)
        if not os.path.isdir(pair_dir) or pair.startswith('_'):
            continue
        for config in sorted(os.listdir(pair_dir)):
            cfg_dir = os.path.join(pair_dir, config)
            if not os.path.isdir(cfg_dir):
                continue
            direct = list(_sim_dbs_in(ChannelRun(pair, config, None, cfg_dir)))
            if direct:
                yield from direct
                continue
            for sub in sorted(os.listdir(cfg_dir)):
                sub_dir = os.path.join(cfg_dir, sub)
                if os.path.isdir(sub_dir):
                    yield from _sim_dbs_in(ChannelRun(pair, config, sub, sub_dir))
