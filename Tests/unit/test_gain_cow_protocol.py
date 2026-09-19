"""test_gain_cow_protocol.py — a copy-on-write aisle view IS its eager copy, method by method.

WHY THIS EXISTS BESIDE `test_gain_cow_equivalence.py`.  That file proves the three views end
to end: purity (the live dict never moves), saving (the view copies strictly less than the
copy), and equivalence (three arms reach a byte-identical warehouse).  What it does NOT do is
exercise the MAPPING PROTOCOL directly — `__contains__`, `__iter__`, `__len__`, `keys`,
`items`, `values`, `get`, `__setitem__` are reached only incidentally, through whichever of
them a placement arm happens to call.

That gap matters the moment the three classes stop being three independent copies of the same
eight methods.  `_CowFloats`, `_CowSets` and `_CowListsByKey` differ ONLY in how a miss
materializes; every other method was written out three times, byte for byte.  Hoisting the
shared eight into a base is safe exactly to the degree that something asserts they behaved
identically first — so this file asserts it, against an oracle that knows nothing about the
view implementation.

THE ORACLE IS THE EAGER COPY.  `AISLE_COPIERS` is the frozen reference the views were built
to replace: `_copy_of_floats`, `_copy_of_sets`, `_copy_of_lists_by_key` produce the plain
`defaultdict` a pool used to be handed.  Every assertion below compares the view against that
copy of the SAME live dict, so nothing here recomputes an expected value the way the code
under test does.
"""

#: NO sys.path bootstrap here: `Tests/conftest.py` puts the repo root on the path for
#: the whole suite, and CLAUDE.md names it and entry-script bootstraps as the only
#: legal `sys.path.insert` sites.
import pytest


from Inbound import gain


# ── the three shapes, each with a live dict of its own kind ───────────────────────
# `name` is the manager attribute, so the view/copier pair is looked up exactly as
# production looks it up — a shape that fell out of both tables would fail collection
# rather than quietly skip.

# THE TABLES ARE READ THROUGH THEIR MODULE, and this file is the reason.  It REBINDS
# `AISLE_VIEWS` to sabotage the views; a `from ... import AISLE_VIEWS` anywhere in the
# chain would bind the name at import and never see the rebinding, so the sabotage would
# stop biting and this file would go on passing.  `Inbound.gain` does not re-export them.
from Inbound import gain_cow                                        # noqa: E402


def _live_floats():
    return {3: 2.5, 1: 0.0, 2: -1.25}


def _live_sets():
    return {3: {10, 11}, 1: set(), 2: {12}}


def _live_lists():
    return {3: {7: [1.0, 2.0]}, 1: {}, 2: {8: [3.0]}}


SHAPES = [
    ('aisle_demand_sum', _live_floats),
    ('aisle_sku_sets', _live_sets),
    ('aisle_member_pos', _live_lists),
]
IDS = [s[0] for s in SHAPES]


def _pair(name, make_live):
    """(view, eager_copy) over two INDEPENDENT copies of the same live dict.

    Independent because the eager copy is the oracle: sharing one live dict would let a
    write through the view show up in the oracle and make every comparison vacuous.
    """
    view = gain_cow.AISLE_VIEWS[name](make_live())
    eager = gain_cow.AISLE_COPIERS[name](make_live())
    return view, eager


def _frozen(v):
    """A comparable rendering of a value of any of the three shapes."""
    if isinstance(v, set):
        return ('set', sorted(v))
    if isinstance(v, dict) or hasattr(v, 'items'):     # a dict, or the lists-by-key inner view
        return ('dict', sorted((k, list(xs)) for k, xs in v.items()))
    return ('num', float(v))


# ── read-only parity ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_len_matches_the_eager_copy(name, make_live):
    view, eager = _pair(name, make_live)
    assert len(view) == len(eager)


@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_iteration_yields_the_same_keys_in_the_same_order(name, make_live):
    """ORDER, not just membership: a pool's candidate walk is order-sensitive."""
    view, eager = _pair(name, make_live)
    assert list(view) == list(eager)
    assert view.keys() == list(eager.keys())


@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_contains_matches_for_present_and_absent_keys(name, make_live):
    view, eager = _pair(name, make_live)
    for k in (1, 2, 3, 99, -1):
        assert (k in view) == (k in eager), f'key {k} disagreed'


@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_getitem_matches_for_every_present_key(name, make_live):
    view, eager = _pair(name, make_live)
    for k in list(eager):
        assert _frozen(view[k]) == _frozen(eager[k]), f'key {k} disagreed'


@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_items_and_values_match_the_eager_copy(name, make_live):
    view, eager = _pair(name, make_live)
    assert [(k, _frozen(v)) for k, v in view.items()] == \
           [(k, _frozen(v)) for k, v in eager.items()]
    assert [_frozen(v) for v in view.values()] == [_frozen(v) for v in eager.values()]


# ── the defaultdict semantics a miss inherits ─────────────────────────────────────

@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_a_missing_key_materializes_exactly_as_the_defaultdict_does(name, make_live):
    """The views replaced `defaultdict`s, so a miss must CREATE, not raise."""
    view, eager = _pair(name, make_live)
    before = len(eager)
    assert _frozen(view[404]) == _frozen(eager[404]), 'the created value differed'
    assert len(view) == len(eager) == before + 1, 'the miss did not create in both'
    assert 404 in view and 404 in eager


@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_a_created_key_sorts_last_in_iteration(name, make_live):
    view, eager = _pair(name, make_live)
    view[404], eager[404]                       # noqa: B018 — the access IS the creation
    assert list(view) == list(eager)
    assert list(view)[-1] == 404


# ── get() ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_get_matches_with_and_without_a_default(name, make_live):
    """`get` must NOT create — that is the whole reason a pool ever calls it."""
    view, eager = _pair(name, make_live)
    for k in (1, 2, 3, 99):
        assert _frozen(view.get(k, 0.0)) == _frozen(eager.get(k, 0.0)), f'key {k} disagreed'
    assert view.get(99) is None and eager.get(99) is None
    assert len(view) == len(eager), 'get() created an entry'


@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_get_reads_an_overlaid_value_not_the_live_one(name, make_live):
    """After a write-through, `get` must see the OVERLAY — the pool's own bookkeeping."""
    view, _ = _pair(name, make_live)
    sentinel = view[3]                           # materialize (or fall through) first
    view[3] = sentinel
    assert view.get(3) is sentinel


# ── writes, and the purity rule they must not break ───────────────────────────────

@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_setitem_is_visible_to_every_reader_and_invisible_to_the_live_dict(name, make_live):
    live = make_live()
    snapshot = {k: _frozen(v) for k, v in live.items()}
    view = gain_cow.AISLE_VIEWS[name](live)

    fresh = view[404]                            # a materialized value of the right shape
    view[7] = fresh
    assert view[7] is fresh
    assert 7 in view
    assert view.get(7) is fresh
    assert dict(view.items())[7] is fresh
    assert 7 in list(view)
    assert {k: _frozen(v) for k, v in live.items()} == snapshot, \
        'a write through the view reached the live dict'


@pytest.mark.parametrize('name,make_live', SHAPES, ids=IDS)
def test_a_write_through_getitem_never_reaches_the_live_dict(name, make_live):
    """The mutable shapes are the dangerous ones: `d[aid].add(...)` / `.append(...)`."""
    live = make_live()
    snapshot = {k: _frozen(v) for k, v in live.items()}
    view = gain_cow.AISLE_VIEWS[name](live)

    got = view[3]
    if isinstance(got, set):
        got.add(999)
    elif isinstance(got, dict) or hasattr(got, 'items'):     # the lists-by-key inner view
        got[7].append(999.0)
    else:
        view[3] = got + 999.0
    assert {k: _frozen(v) for k, v in live.items()} == snapshot, \
        'the view handed out (or wrote into) the live container'


# ── the base-class hoist itself ───────────────────────────────────────────────────

def test_the_three_views_share_one_implementation_of_the_mapping_protocol():
    """The eight methods that never differed are defined ONCE, not three times.

    Stated as a property of the classes rather than a line count: if a later change
    re-specialises one of them, this names which.  `__getitem__` is the ONE method a shape
    is allowed to own, because how a miss materializes IS the difference between them.
    """
    views = [gain_cow._CowFloats, gain_cow._CowSets, gain_cow._CowListsByKey]
    shared = ('__setitem__', 'get', '__contains__', '__iter__', '__len__',
              'keys', 'items', 'values', '__init__')
    for meth in shared:
        impls = {getattr(v, meth) for v in views}
        assert len(impls) == 1, \
            f'{meth} has {len(impls)} implementations across the three views; it never differed'
    getters = {v.__getitem__ for v in views}
    assert len(getters) == 3, \
        'the three views must each own __getitem__ — how a miss materializes is their difference'


def test_every_view_is_slotted_so_a_pool_cannot_grow_state_on_it():
    """A view is opened per pool; a stray attribute would be per-pool heap nobody frees."""
    for v in (gain_cow._CowFloats, gain_cow._CowSets, gain_cow._CowListsByKey):
        inst = v({})
        with pytest.raises(AttributeError):
            inst.scratch = 1
