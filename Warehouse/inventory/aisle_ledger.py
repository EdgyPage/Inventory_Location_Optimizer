"""aisle_ledger.py — what each aisle currently holds, priced.

The **aisle ledger** (CONTEXT.md) is one concept kept in eleven parallel dicts: which SKUs an
aisle holds, which matrix indices they translate to, how many bins each occupies, where those
bins sit, and the four priced LEVELS that membership implies — lift, demand, pick load and
cart volume — plus the three per-SKU products those levels are summed from.

    ┌─ membership ──────────┬─ priced levels ────────┬─ per-SKU products ──────────┐
    │ sku_sets              │ lift_sum               │ sku_demand_product          │
    │ idx_sets              │ demand_sum             │ sku_pick_load_product       │
    │ sku_counts            │ pick_load_sum          │ sku_vol_product             │
    │ member_pos            │ vol_sum                │                             │
    └───────────────────────┴────────────────────────┴─────────────────────────────┘

Why this module exists
----------------------
The eleven dicts had TWO writers who did not know about each other.  The *add* half lived
inside the placement policies — ten hand-copied commit blocks in `Assignment_Functions.py`,
each maintaining a DIFFERENT SUBSET — while `_execute_placement` committed only `sku_counts`.
The *drop* half lived in `inventory_reorder.py` in two copies, the second a hoisted-locals
inline twin whose docstring asked a human to KEEP THE TWO IN SYNC.

They were not in sync, and nothing could have said so:

- `vol_sum` was incremented by `rank_cartlabor` and decremented by neither drop path, so the
  cart-swap penalty that arm competes on grew for the length of a run.  It is READ in the
  scoring expression, so it moved placements, not just a recorded number.
- `lift_sum` was decremented by both drop paths and incremented only by a production-dead
  family, so it decayed monotonically in every shipped arm.

Both are the same failure: a quantity whose add and drop live in different files, maintained by
different people at different times, with no place that owns the pair.

What is and is NOT in here
--------------------------
`init_lift_state` on the manager does two jobs and only one of them is the ledger's — it also
rebuilds `_bin_sku`, `_current_quantities` and the per-SKU bin indexes, none of which are aisle
state.  Those stay on the manager.  This module owns the aisle's own books and nothing else.

Per-BIN and per-SKU are separate operations, deliberately
---------------------------------------------------------
`member_pos` records a COLUMN POSITION PER LIVE BIN, while the priced levels are SKU-once.  Four
of the ten historical commit blocks correctly kept the index add and the `member_pos` append
OUTSIDE the `if sku not in sku_sets[aid]` guard for exactly this reason.  So the interface is a
pair at each end — `add_bin`/`add_sku` and `drop_bin`/`drop_sku` — and the guard stays at the
call site, where each caller already has it.  Collapsing either pair into one method would
quietly change which SKUs' positions are tracked.

Float discipline
----------------
Every level is a running float sum, so its value depends on accumulation ORDER.  `add_sku` takes
the already-multiplied scalar from the caller rather than recomputing it from `f` and `q`: some
historical blocks accumulate `+= f_s * q_s` and others `+= fq` computed earlier, and re-deriving
either would move the last bits.  `drop_sku` clamps at zero exactly as the hand-written
decrements did.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

__all__ = ['AisleLedger']


class AisleLedger:
    """The aisle ledger: eleven dicts, one owner, one add/drop pair at each grain.

    The dicts are exposed as plain attributes, not properties.  They are read on the placement
    hot path by closures that hoist them once outside their loop, and an indirection there is a
    per-candidate cost — the same argument `inventory_zoning` records for staying a mixin.
    """

    __slots__ = ('sku_sets', 'idx_sets', 'sku_counts', 'member_pos',
                 'lift_sum', 'demand_sum', 'pick_load_sum', 'vol_sum',
                 'sku_demand_product', 'sku_pick_load_product', 'sku_vol_product')

    def __init__(self) -> None:
        # ── membership ────────────────────────────────────────────────────────────────
        self.sku_sets: dict[int, set[int]] = defaultdict(set)
        self.idx_sets: dict[int, set[int]] = defaultdict(set)
        self.sku_counts: dict[int, dict[int, int]] = defaultdict(dict)
        self.member_pos: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        # ── priced levels ─────────────────────────────────────────────────────────────
        self.lift_sum: dict[int, float] = defaultdict(float)
        self.demand_sum: dict[int, float] = defaultdict(float)
        self.pick_load_sum: dict[int, float] = defaultdict(float)
        self.vol_sum: dict[int, float] = defaultdict(float)
        # ── per-SKU products the levels are summed from ───────────────────────────────
        self.sku_demand_product: dict[int, float] = {}      # sku -> f * q
        self.sku_pick_load_product: dict[int, float] = {}   # sku -> f * q * cost1
        self.sku_vol_product: dict[int, float] = {}         # sku -> f * q * volume

    # ── reads ─────────────────────────────────────────────────────────────────────────

    def holds(self, aid: int, sku: int) -> bool:
        """Is this SKU resident in this aisle?  The guard every commit block already uses."""
        return sku in self.sku_sets[aid]

    def members(self, aid: int) -> set[int]:
        return self.sku_sets[aid]

    def bin_count(self, aid: int, sku: int) -> int:
        return self.sku_counts[aid].get(sku, 0)

    # ── the drop pair ─────────────────────────────────────────────────────────────────
    #
    # The two callers (`_drop_sku_from_aisle` and the loop inside `_reclaim_empty_bins`) were
    # two hand-maintained copies of this logic.  They are now one body.

    def drop_bin(self, aid: int, idx: int | None, x_phys: float) -> None:
        """Retire ONE bin's column position.  Called for every reclaimed bin, live-bin only."""
        if idx is None:
            return
        mp = self.member_pos.get(aid)
        if mp is None:
            return
        xs = mp.get(idx)
        if xs:
            try:
                xs.remove(x_phys)
            except ValueError:
                pass
            if not xs:
                del mp[idx]

    def drop_sku(self, aid: int, sku: int, idx: int | None,
                 lift_delta_fn=None, *, last_when_zero: bool = False) -> bool:
        """Decrement the SKU's bin count; on its LAST bin, retire it and subtract its levels.

        Returns True when the SKU left the aisle.  Every subtraction is clamped at zero, as
        the two hand-written copies were.

        `lift_delta_fn` is a CALLABLE, not a scalar, and the distinction is load-bearing: the
        original computed `2.0 * affinity.delta_lift_idxs(sku, idx_sets[aid])` *after* the
        index was discarded, so a precomputed value would be taken against a different set and
        change the result.  It is called with the post-discard index set.  Passing a callable
        also keeps this module free of any affinity knowledge.

        `last_when_zero` reproduces the difference between the two original copies: the cold
        path used `elif n == 1`, so a defensive n == 0 fell through and did nothing; the hot
        twin used a bare `else`, treating n == 0 like n == 1.  The twin's callers pass True.
        """
        counts = self.sku_counts[aid]
        n = counts.get(sku, 0)
        if n > 1:
            counts[sku] = n - 1
            return False
        if n == 0 and not last_when_zero:
            return False

        counts.pop(sku, None)
        self.sku_sets[aid].discard(sku)
        if idx is not None:
            self.idx_sets[aid].discard(idx)

        if lift_delta_fn is not None:
            # Unconditional, exactly as both originals were: the assignment also MATERIALISES
            # the defaultdict entry, and guarding it on a non-zero delta would silently stop
            # creating keys that `aisle_metrics` later reads.
            self.lift_sum[aid] = max(0.0, self.lift_sum[aid] - lift_delta_fn(self.idx_sets[aid]))
        d = self.sku_demand_product.get(sku, 0.0)
        if d:
            self.demand_sum[aid] = max(0.0, self.demand_sum[aid] - d)
        dl = self.sku_pick_load_product.get(sku, 0.0)
        if dl:
            self.pick_load_sum[aid] = max(0.0, self.pick_load_sum[aid] - dl)
        dv = self.sku_vol_product.get(sku, 0.0)
        if dv:
            self.vol_sum[aid] = max(0.0, self.vol_sum[aid] - dv)
        return True

    # ── the invariant ─────────────────────────────────────────────────────────────────

    def reconcile(self, *, tol: float = 1e-6) -> list[str]:
        """Every priced level must equal the sum of its members' products.  [] when sound.

        This is the assertion the eleven loose dicts made impossible to write, and the reason
        both drifts survived for the life of their features.  Returns findings rather than
        raising so a caller can report them all at once.

        `lift_sum` is deliberately NOT checked: it is not a sum over per-SKU products but a
        pairwise quantity over the aisle's index set, so reconciling it needs the affinity store.
        It is also write-only end to end and is being deleted.
        """
        out: list[str] = []
        for level_name, product_name in (('demand_sum', 'sku_demand_product'),
                                         ('pick_load_sum', 'sku_pick_load_product'),
                                         ('vol_sum', 'sku_vol_product')):
            level = getattr(self, level_name)
            product = getattr(self, product_name)
            if not product:
                continue          # never seeded for this run — nothing to reconcile against
            for aid, sku_set in self.sku_sets.items():
                by_hand = sum(product.get(s, 0.0) for s in sku_set)
                have = level.get(aid, 0.0)
                if abs(have - by_hand) > tol:
                    out.append(
                        f'{level_name}[{aid}] = {have!r} but its {len(sku_set)} members '
                        f'sum to {by_hand!r} (drift {have - by_hand:+.6g})')
        return out

    def assert_sound(self) -> None:
        """`reconcile()` as an assertion, for tests and end-of-drain checks."""
        findings = self.reconcile()
        assert not findings, 'aisle ledger is inconsistent:\n  ' + '\n  '.join(findings)
