"""profile_resolver.py — the generic resolver over the profiles-tree contract.

The catalogue analogue of ``runschema.resolver.RunTree``, deliberately NOT a subclass (Schema
must not import Optimization): one class interpreting the ``Schema.profile_tree`` declaration
via the shared ``Schema.pathtpl`` vocabulary.

Descriptor-first, walk-fallback: a profile run carrying ``profile_layout.json`` is served from
the descriptor (which also carries the binding a simulation records); a pre-contract run — every
catalogue generated before the descriptor existed, forever, per the no-backfill decision — is
served by a walk that mirrors ``runschema.runlayout._profile_pairs`` byte-for-byte.  The label
format ``f'{run}__{profile}'`` is the run tree's ``<pair>`` level and must never drift.
"""
from __future__ import annotations

import logging
import os

from Schema import profile_tree as _decl
from Schema.pathtpl import render

log = logging.getLogger(__name__)


class ProfileTree:
    """Resolver for one profiles ROOT (the directory holding profile-run dirs).

    Construct via ``ProfileTree(profiles_root)``; each accessor takes the profile-run name where
    scope demands it.  Existence is not implied — optional artifacts legitimately do not exist.
    """

    def __init__(self, profiles_root: str, contract: dict | None = None):
        self.root = os.path.abspath(profiles_root)
        self.contract = contract or _decl.load(_decl.head() or '') or _decl.build()
        self.artifacts = self.contract['artifacts']

    # ── identity ───────────────────────────────────────────────────────────────
    @property
    def schema_id(self) -> str:
        return self.contract['schema_id']

    def __repr__(self) -> str:                                # pragma: no cover - debug aid
        return f'<ProfileTree {_decl.short_id(self.schema_id)} {os.path.basename(self.root)}>'

    # ── descriptors ────────────────────────────────────────────────────────────
    def layout_of(self, profile_run: str) -> dict | None:
        """The run's own descriptor, or None for a pre-contract catalogue (normal, not an error)."""
        return _decl.read_profile_layout(os.path.join(self.root, profile_run))

    # ── navigation ─────────────────────────────────────────────────────────────
    def runs(self) -> list:
        """Profile-run names under the root, ascending.  Disk is the authority for EXISTENCE
        (a descriptor names only what its generator wrote; a run dir is real either way)."""
        if not os.path.isdir(self.root):
            return []
        return sorted(d for d in os.listdir(self.root)
                      if os.path.isdir(os.path.join(self.root, d)))

    def path(self, artifact: str, profile_run: str, **parts) -> str:
        """Absolute path of any contract artifact within one profile run."""
        spec = self.artifacts[artifact]
        rel = render(spec['path'], **parts)
        return os.path.join(self.root, profile_run, rel.replace('/', os.sep))

    def pairs(self, profile_run: str) -> list:
        """(label, inventory_db, affinity_db) for every complete profile in one run.

        Descriptor-first: profiles listed in `profile_layout.json`, resolved through the
        contract templates, existence-checked (a descriptor is a claim; the DB is the fact).
        Fallback: the byte-for-byte mirror of `runlayout._profile_pairs` — sorted listdir,
        literal `inventory/inventory.db` + `affinity/affinity.db`, both-exist filter.
        The label is ALWAYS `f'{profile_run}__{profile}'` — the run tree's pair level.
        """
        run_dir = os.path.join(self.root, profile_run)
        layout = self.layout_of(profile_run)
        out = []
        if layout is not None:
            for profile in sorted(layout.get('profiles') or {}):
                inv = self.path('inventory_db', profile_run, profile=profile)
                aff = self.path('affinity_db', profile_run, profile=profile)
                if os.path.exists(inv) and os.path.exists(aff):
                    out.append((f'{profile_run}__{profile}', inv, aff))
            return out
        # pre-contract: the legacy walk, unchanged semantics
        if not os.path.isdir(run_dir):
            return out
        for profile in sorted(os.listdir(run_dir)):
            pdir = os.path.join(run_dir, profile)
            if not os.path.isdir(pdir):
                continue
            inv = os.path.join(pdir, 'inventory', 'inventory.db')
            aff = os.path.join(pdir, 'affinity', 'affinity.db')
            if os.path.exists(inv) and os.path.exists(aff):
                out.append((f'{profile_run}__{profile}', inv, aff))
        return out

    def latest(self) -> str | None:
        """The newest profile run with at least one complete pair.

        Descriptor `created` timestamps order the descriptor-bearing runs; runs WITHOUT a
        descriptor order by name (the historical lexicographic behavior, which their timestamped
        names make correct).  When both kinds exist, the maximum across the merged order wins;
        if that disagrees with the pure lexicographic answer, a warning names both — the
        situation is legal (a backdated --name) but worth a human glance.
        """
        candidates = [r for r in self.runs() if self.pairs(r)]
        if not candidates:
            return None
        lex = candidates[-1]

        def _key(run: str):
            layout = self.layout_of(run)
            created = (layout or {}).get('created') or ''
            return (created, run)          # ISO timestamps sort textually; name breaks ties

        best = max(candidates, key=_key)
        if best != lex:
            log.warning('profile run ordering: descriptor timestamps pick %r but the '
                        'lexicographic (legacy) order picks %r - using the descriptor', best, lex)
        return best

    def binding_of(self, label: str) -> dict | None:
        """The catalogue-version binding a RUN should record for one pair label, or None.

        None whenever the label's profile run has no descriptor — with descriptors forward-only,
        that is every pre-contract catalogue, and recording `null` is honest (absence of
        evidence, never fabricated evidence).
        """
        profile_run, sep, profile = label.partition('__')
        if not sep:
            return None
        layout = self.layout_of(profile_run)
        if layout is None:
            return None
        entry = (layout.get('profiles') or {}).get(profile)
        if entry is None:
            return None
        return {
            'profile_run': profile_run,
            'profile': profile,
            'profile_schema_id': layout.get('schema_id'),
            'params_digest': {
                'inventory': (entry.get('inventory') or {}).get('params_digest'),
                'affinity': (entry.get('affinity') or {}).get('params_digest'),
            },
        }
