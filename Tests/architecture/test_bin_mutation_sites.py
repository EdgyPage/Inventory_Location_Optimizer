"""test_bin_mutation_sites.py — the bin-mutation log is complete only while this list is.

`Optimization/metrics/bin_recorder.py` claims completeness *by construction*: bin state can only
change at the sites below, and the recorder covers the two that were unrecorded. That argument
holds exactly as long as no sixth site appears — and the previous scheme became lossy in precisely
this way, silently, with every gate green.

So this greps the domain engine for writes to `Aisle.Bin.storage` / `.storage.quantity` and fails
**by name** if the set of sites changes. Same idiom as `context/guards/path_guard.py`: assert
against a committed allowlist rather than trusting a docstring.

If this fails, do not just update the list. Decide first whether the new site needs a log event —
if it mutates a bin and emits nothing, spatial reconstruction is lossy again.

    python -m pytest Tests/architecture/test_bin_mutation_sites.py -q
"""
from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DOMAIN = os.path.join(_ROOT, 'Warehouse')

# `bin_.storage = ...`, `b.storage = ...`, `bin_.storage.quantity -= ...`, etc.
_MUTATION = re.compile(r'\.storage\s*=(?!=)|\.storage\.quantity\s*(?:[-+*/]?=)(?!=)')

#: Every place bin state can change, and what records it.  file -> {line: why}
#: Verified exhaustively; see bin_recorder.py's docstring for the completeness argument.
#: `Aisle_Storage.py` is deliberately ABSENT: `self.storage: StorageUnit | None = None` is an
#: annotated declaration in `Bin.__init__`, not a mutation of an existing bin, and the regex
#: correctly does not count it.  A real mutation appearing there would be flagged.
ALLOWED = {
    'Warehouse/inventory/Inventory_Management.py':
        '_execute_placement (storage = unit) and _execute_topup (storage.quantity += n, ADR-0003) — both PLACE, both recorded',
    'Warehouse/inventory/inventory_reorder.py': 'requeue_bin — EVICT, recorded',
    'Warehouse/picking/fast_pick.py': 'production pick depletion — PICK, in `picks`',
    'Warehouse/picking/Pick.py': 'legacy pick depletion — PICK, in `picks`',
    'Warehouse/layout/Storage_Primitive.py': 'StorageCart.add_from_bin — DEAD, zero callers',
}


def _mutation_sites() -> dict:
    """{relpath: [line numbers]} for every bin-state write in Warehouse/."""
    found: dict[str, list] = {}
    for dirpath, _dirs, files in os.walk(_DOMAIN):
        for fn in files:
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, _ROOT).replace(os.sep, '/')
            with open(path, encoding='utf-8') as fh:
                for n, line in enumerate(fh, 1):
                    code = line.split('#', 1)[0]
                    if _MUTATION.search(code):
                        found.setdefault(rel, []).append(n)
    return found


def test_no_unrecorded_bin_mutation_site_exists():
    """A new file writing bin state means the log is no longer complete."""
    found = _mutation_sites()
    unexpected = sorted(set(found) - set(ALLOWED))

    assert not unexpected, (
        'bin state is mutated in a file the recorder does not know about:\n  '
        + '\n  '.join(f'{f} lines {found[f]}' for f in unexpected)
        + '\n\nEvery such site must emit a log event, or spatial reconstruction is lossy '
          'again. See Optimization/metrics/bin_recorder.py.')


def test_every_allowed_site_still_exists():
    """The allowlist must not rot in the other direction either.

    A file that stops mutating bins is fine, but the entry should go — a stale allowlist is how
    you end up permitting a site that no longer means what the comment says.
    """
    found = _mutation_sites()
    gone = sorted(set(ALLOWED) - set(found))
    assert not gone, (f'allowlist names files that no longer mutate bin state: {gone} — '
                      f'remove them so the list keeps meaning something')


def test_the_two_recorded_sites_are_the_ones_the_recorder_wraps():
    """The recorder wraps `_execute_placement` and `requeue_bin`; those must be the PLACE and
    EVICT sites, or it is wrapping the wrong methods."""
    from Optimization.metrics import bin_recorder

    src = open(bin_recorder.__file__, encoding='utf-8').read()
    assert '_execute_placement' in src
    assert 'requeue_bin' in src

    found = _mutation_sites()
    assert 'Warehouse/inventory/Inventory_Management.py' in found, 'PLACE site vanished'
    assert 'Warehouse/inventory/inventory_reorder.py' in found, 'EVICT site vanished'


def test_the_dead_site_is_still_dead():
    """`StorageCart.add_from_bin` mutates a bin but has zero callers, which is the only reason
    it needs no log event. If something starts calling it, that stops being true."""
    callers = []
    for dirpath, _dirs, files in os.walk(_ROOT):
        if any(skip in dirpath for skip in ('.git', '__pycache__', 'docs', 'site')):
            continue
        for fn in files:
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            # Skip the definition itself, and this file (which names it in prose).
            if (path.endswith(os.path.join('layout', 'Storage_Primitive.py'))
                    or os.path.abspath(path) == os.path.abspath(__file__)):
                continue
            with open(path, encoding='utf-8') as fh:
                if 'add_from_bin' in fh.read():
                    callers.append(os.path.relpath(path, _ROOT))

    assert not callers, (f'add_from_bin now has callers: {callers} — it mutates a bin and emits '
                         f'no event, so it needs one')
