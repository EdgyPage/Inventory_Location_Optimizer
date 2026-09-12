"""test_site_scorecard_columns.py — the per-channel SHARE column exists only at site scope.

Site-dock 24.  `yard.scorecard` renders at TWO scopes over ONE body: a leaf's yard on an
uncoupled run, and the site's on a coupled one.  The site table carries one column the leaf
table does not — each channel's SHARE of the site receiving crew's busy time, printed beside
the site number and never instead of it (memory `a-right-site-total-hides-two-wrong-shares`:
put-away's site load was 0.9% exact while both per-channel bands failed in OPPOSITE
directions).

WHY THIS IS ITS OWN FILE RATHER THAN A LINE IN THE FIGURE TESTS.  The column was added
unconditionally first, and that is a REGRESSION the ticket's own acceptance could not have
caught: "uncoupled analysis output is byte-identical" was measured on DB ROWS, and figures
are deliberately not diffed (a HEAD-vs-HEAD control differs on every PNG).  So every
archived standing-yard run would have re-rendered `absolute_yard_scorecard.png` with an
extra all-`'-'` column and a changed caption, and nothing in the gate set would have said
so.  The claim needs a test that reads the COLUMN LIST rather than the picture.

Run:  python -m pytest Tests/unit/test_site_scorecard_columns.py -q
"""
from __future__ import annotations

from Optimization.Performance_Evaluations.yard import scorecard as sc


# ── the column list, at both scopes ───────────────────────────────────────────────

def test_the_leaf_table_is_the_column_list_it_has_always_had():
    """An uncoupled leaf's yard IS that channel's, so a "share" column there would print
    100% and assert something nobody asked."""
    assert sc._cols(False) == sc._COLS
    assert sc._SITE_COL not in sc._COLS


def test_the_site_table_inserts_the_share_column_beside_the_number_it_is_a_share_of():
    """BESIDE, not instead of: the site figure and its split have to be readable together
    or the split is a second number nobody can place."""
    cols = sc._cols(True)
    assert sc._SITE_COL in cols
    assert cols.index(sc._SITE_COL) == cols.index('receiver busy') + 1
    assert [c for c in cols if c != sc._SITE_COL] == list(sc._COLS), (
        'the site table changed a column other than the one it adds')


def test_the_two_tables_differ_by_exactly_one_column():
    assert len(sc._cols(True)) == len(sc._cols(False)) + 1


# ── the registration that decides which one renders ───────────────────────────────

def _calls(monkeypatch):
    seen = []
    monkeypatch.setattr(sc, 'render', lambda ctx, params, **kw: seen.append(kw))
    return seen


def test_the_site_registration_renders_the_site_table(monkeypatch):
    """The wiring, not the picture: `render_site` is the only caller that asks for the
    column, so a forwarding that dropped the flag would silently render the LEAF table into
    the site tree — a coupled run's scorecard with no split in it at all."""
    seen = _calls(monkeypatch)
    sc.render_site(object(), {})
    assert seen == [{'site': True}]
