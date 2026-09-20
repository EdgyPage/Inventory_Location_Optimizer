"""test_pick_owed.py — what the planned demand costs to serve from where the stock IS.

Phase 2 ranks unloading policies, and until now it scored them on `ss_prod_total`, a mean
of per-batch production seconds.  An unloading policy changes only WHEN stock reaches a
shelf, never how much work the run contains, so that quantity is invariant to it: the
ranking on record came out an exact tie on every cell and fell through to name order.

`pick_owed` is the replacement — the pick seconds the PLANNED batches will cost from the
current placement.  The arithmetic is `per_pick`, shared with `_optimal_work_assign` so the
score and the floor it is read against cannot drift apart.

Pinned here, every number computed by hand from a four-bin warehouse:

  1. the score is the demand-weighted mean over each SKU's OWN bins -- not a sum, because a
     line is served from one location and a SKU in four bins is not four picks;
  2. moving one hot SKU to a far, high bin raises it, and the rise is exactly the cost
     difference of the two bins;
  3. a planned SKU with nothing on a shelf adds NOTHING to the score and its whole weight
     to `unservable` -- the pairing that stops "leave it in the yard" from ranking first;
  4. the demand weight is what the SCRIPT asks for, so two placements identical except in
     a SKU nobody ordered score the same;
  5. unarmed, it is inert.

Run:  python -m pytest Tests/unit/test_pick_owed.py -q
"""
from __future__ import annotations

from Warehouse.inventory import inventory_optimal as io_mod


# ── a warehouse of four bins, two heights, built without the sim harness ────────
# x_phys is the only travel term that varies (y_phys drives the height bracket), so
# every expected number below is a one-line hand computation.

class _Bin:
    __slots__ = ('x_phys', 'y_phys', 'storage')

    def __init__(self, x, y):
        self.x_phys, self.y_phys = float(x), float(y)
        self.storage = None


class _Storage:
    def __init__(self, order, quantity=1):
        self.order, self.quantity = order, quantity


class _Order:
    def __init__(self, sku):
        self.sku = sku


class _WP:
    # 1 ft/s in both axes, so sec_per_inch is the same constant on each and the travel
    # term is (x + y) * that pace -- see `sec_per_inch`, which is what the code applies.
    x_speed = y_speed = 1.0
    pick_intercept = 10.0
    pick_per_item = 0.0          # off, so the handling term is exactly q * v
    height_brackets = ((100.0, 1.0), (float('inf'), 2.0))


class _Mgr(io_mod.OptimalLayoutMixin):
    """Just enough manager: `enable_pick_owed`/`pick_owed` read `_unavailable` and the
    `_po_*` state they set themselves."""

    def __init__(self, bins):
        self._bins = bins
        self._unavailable = {}
        self._po_weight = None
        self._po_hand = {}
        self._po_x = self._po_y = 0.0
        self._po_brackets = ()

    @staticmethod
    def _handle_var(order, wp):
        return 1.0               # v_s = 1 for every SKU, so h_s = intercept + q

    def place(self, bin_, order, quantity=1):
        bin_.storage = _Storage(order, quantity)
        self._unavailable[id(bin_)] = bin_


_PACE = io_mod.sec_per_inch(1.0)          # the per-inch pace both axes share here


def _cost(bin_, hand):
    """The expression `pick_owed` accumulates, restated for the expectations below."""
    m = 2.0 if bin_.y_phys >= 100.0 else 1.0
    return _PACE * (bin_.x_phys + bin_.y_phys) + m * hand


def _armed(weights, qtys=None):
    """A manager over four bins, armed with `weights` as the planned demand."""
    near_low, far_low, near_high, far_high = (_Bin(0, 0), _Bin(100, 0),
                                              _Bin(0, 100), _Bin(100, 100))
    mgr = _Mgr([near_low, far_low, near_high, far_high])
    orders = [_Order(s) for s in weights]
    qtys = qtys or {s: 1.0 for s in weights}
    mgr.enable_pick_owed(weights, qtys, orders, _WP())
    return mgr, (near_low, far_low, near_high, far_high)


# ── 1. the mean over a SKU's own bins ───────────────────────────────────────────

def test_the_score_is_the_mean_over_a_skus_bins_not_the_sum():
    """One line is served from ONE location.  Summing would make a well-stocked SKU read
    as expensive and invert the whole metric."""
    mgr, (near_low, far_low, _nh, _fh) = _armed({7: 3.0})
    hand = mgr._po_hand[7]
    mgr.place(near_low, _Order(7))
    one_bin, unservable = mgr.pick_owed()
    assert unservable == 0.0
    assert one_bin == 3.0 * _cost(near_low, hand)

    mgr.place(far_low, _Order(7))                       # the SAME sku, a second bin
    two_bins, _ = mgr.pick_owed()
    expected = 3.0 * ((_cost(near_low, hand) + _cost(far_low, hand)) / 2)
    assert two_bins == expected
    assert one_bin < two_bins < 2 * one_bin, (
        'a second, worse bin must move the score between the two, not double it')


# ── 2. a worse bin costs more, by exactly the bins difference ───────────────────

def test_moving_a_hot_sku_to_a_far_high_bin_raises_the_score_by_the_bin_gap():
    mgr, (near_low, _fl, _nh, far_high) = _armed({7: 4.0})
    hand = mgr._po_hand[7]
    mgr.place(near_low, _Order(7))
    good, _ = mgr.pick_owed()

    mgr._unavailable.clear()
    near_low.storage = None
    mgr.place(far_high, _Order(7))
    bad, _ = mgr.pick_owed()

    assert bad > good
    assert bad - good == 4.0 * (_cost(far_high, hand) - _cost(near_low, hand))


def test_the_height_bracket_is_in_the_score():
    """Sigma f*D is height-blind; this is one of the two reasons it could not be reused."""
    mgr, (near_low, _fl, near_high, _fh) = _armed({7: 1.0})
    hand = mgr._po_hand[7]
    mgr.place(near_low, _Order(7))
    low, _ = mgr.pick_owed()
    mgr._unavailable.clear(); near_low.storage = None
    mgr.place(near_high, _Order(7))
    high, _ = mgr.pick_owed()
    # the same aisle distance in x; the bracket doubles the handling term and y adds travel
    assert high - low == (_PACE * 100.0) + hand


# ── 3. absence is reported, never priced as free ────────────────────────────────

def test_a_planned_sku_with_no_shelf_stock_scores_nothing_and_is_reported():
    """THE INVERSION THIS PAIRING PREVENTS.  An absent SKU contributes nothing to the
    seconds, so a caller ranking on seconds alone would rank 'leave it in the yard' first.
    The second number is what says how much demand the first one declined to price."""
    mgr, (near_low, _fl, _nh, _fh) = _armed({7: 3.0, 9: 5.0})
    hand = mgr._po_hand[7]
    mgr.place(near_low, _Order(7))                      # sku 9 is nowhere

    owed, unservable = mgr.pick_owed()
    assert owed == 3.0 * _cost(near_low, hand), 'the absent SKU must not add seconds'
    assert unservable == 5.0, 'its whole planned weight must be reported'

    # and shelving it RAISES the seconds while clearing the unservable weight -- so the
    # two numbers can only be read together.
    mgr.place(_nh, _Order(9))
    owed_after, unservable_after = mgr.pick_owed()
    assert owed_after > owed and unservable_after == 0.0


# ── 4. the weight is the planned demand ─────────────────────────────────────────

def test_a_sku_nobody_ordered_does_not_move_the_score():
    mgr, (near_low, far_low, _nh, _fh) = _armed({7: 2.0})
    mgr.place(near_low, _Order(7))
    before, _ = mgr.pick_owed()
    mgr.place(far_low, _Order(404))                     # on a shelf, absent from the script
    after, unservable = mgr.pick_owed()
    assert after == before and unservable == 0.0


def test_the_planned_quantity_reaches_the_handling_term():
    light, _ = _armed({7: 1.0}, qtys={7: 1.0})
    heavy, _ = _armed({7: 1.0}, qtys={7: 10.0})
    # h_s = intercept + q*per_item + q*v, with per_item 0 and v 1 -> 10 + q
    assert light._po_hand[7] == 11.0
    assert heavy._po_hand[7] == 20.0


# ── 5. inert until armed ────────────────────────────────────────────────────────

def test_unarmed_it_is_inert():
    mgr = _Mgr([])
    assert mgr.pick_owed() == (0.0, 0.0)
