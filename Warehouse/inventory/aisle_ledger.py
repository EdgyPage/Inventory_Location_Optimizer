"""aisle_ledger.py — what each aisle currently holds, priced.

The **aisle ledger** (CONTEXT.md) is one concept kept in ten parallel dicts: which SKUs an
aisle holds, which matrix indices they translate to, how many bins each occupies, where those
bins sit, and the three priced LEVELS that membership implies — demand, pick load and cart
volume — plus the three per-SKU products those levels are summed from.

    ┌─ membership ──────────┬─ priced levels ────────┬─ per-SKU products ──────────┐
    │ sku_sets              │ demand_sum             │ sku_demand_product          │
    │ idx_sets              │ pick_load_sum          │ sku_pick_load_product       │
    │ sku_counts            │ vol_sum                │ sku_vol_product             │
    │ member_pos            │                        │                             │
    └───────────────────────┴────────────────────────┴─────────────────────────────┘

Why this module exists
----------------------
These dicts had TWO writers who did not know about each other.  The *add* half lived
inside the placement policies — ELEVEN hand-copied commit blocks in `Assignment_Functions.py`,
each maintaining one of three different subsets — while `_execute_placement` committed only
`sku_counts`.  (The ticket that opened this counted ten; reading the module found an eleventh,
`_cluster_map_commit`, hiding as a helper next to `_commit_aisle`.)
The *drop* half lived in `inventory_reorder.py` in two copies, the second a hoisted-locals
inline twin whose docstring asked a human to KEEP THE TWO IN SYNC.

They were not in sync, and nothing could have said so:

- `vol_sum` was incremented by `rank_cartlabor` and decremented by neither drop path, so the
  cart-swap penalty that arm competes on grew for the length of a run.  It is READ in the
  scoring expression, so it moved placements, not just a recorded number.
- `lift_sum` was decremented by both drop paths and incremented only by a production-dead
  family, so it decayed monotonically in every shipped arm.  It was write-only end to end —
  no Quantity, figure, view or experiment read its column — so it was DELETED rather than
  repaired, along with the `load_min`/`load_max` family that was its only in-memory reader.

Both are the same failure: a quantity whose add and drop live in different files, maintained by
different people at different times, with no place that owns the pair.

What is and is NOT in here
--------------------------
`init_placement_state` on the manager does two jobs and only one of them is the ledger's — it also
rebuilds `_bin_sku`, `_current_quantities` and the per-SKU bin indexes, none of which are aisle
state.  Those stay on the manager.  This module owns the aisle's own books and nothing else.

Per-BIN and per-SKU are separate operations, deliberately
---------------------------------------------------------
`member_pos` records a COLUMN POSITION PER LIVE BIN, while the priced levels are SKU-once.  Six
of the eleven historical commit blocks correctly kept the index add and the `member_pos` append
OUTSIDE the `if sku not in sku_sets[aid]` guard for exactly this reason; the other five kept no
positions at all.  So the interface
splits at each end — `add_bin`/`add_sku` and `drop_bin`/`drop_sku` — and the guard stays at
the call site, where each caller already has it.  Collapsing either into one method would
quietly change which SKUs' positions are tracked.

The add end has a third method, `count_bin`, because the per-bin COUNT is the one book the
manager maintained rather than the policy.  Its drop-side mirror has no separate method: the
decrement is what decides "last bin", so it is fused into `drop_sku`.

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

__all__ = ['AisleLedger']


class _UnboundBook(dict):
    """A book its ledger was never handed.  Reads see empty; a WRITE is refused.

    `over` binds this ONE shared instance for every book a caller leaves out, instead of
    allocating a private dict per missing book.  That is ten allocations saved per call on
    a path the inbound gain evaluator walks once per VIRTUAL PLACEMENT -- it rebuilds the
    arm's policy every time, at 12.59 pool opens per placement.  Measured in place against
    a real pool open: binding a ledger went from 2.00 us to 0.29 us, from 23.4% of a whole
    pool open down to 4.2%.

    Refusing is also the better failure.  A private empty dict ABSORBS a write that nobody
    can ever read -- a family committing to a book its builder was never given, silently.
    Refusing is what makes ONE shared instance safe to hand every ledger: there is no state
    here for one family's stray write to leave behind for the next.
    """

    __slots__ = ()

    def _refuse(self, *_a, **_k):
        raise TypeError(
            'this aisle book is not bound to the ledger that holds it, so a write here '
            'would go nowhere. A placement family may only write the books its builder '
            'was handed -- see AisleLedger.over and AisleLedger.POLICY_BOOKS.')

    __setitem__ = __delitem__ = setdefault = update = pop = popitem = clear = _refuse

    #: `d[aid]` is how every write starts (`idx_sets[aid].add`, `demand_sum[aid] +=`), and
    #: on a plain empty dict it would surface as a bare `KeyError: 1`.  Routing the miss
    #: through the same refusal says WHY.  `.get(aid, default)` does not come through here,
    #: so reads stay soft and `reconcile` still answers on an unbound level.
    __missing__ = _refuse


#: the single shared stand-in for every book a ledger was not handed.
_UNBOUND = _UnboundBook()


class AisleLedger:
    """The aisle ledger: ten dicts, one owner, one add/drop pair at each grain.

    The dicts are exposed as plain attributes, not properties.  They are read on the placement
    hot path by closures that hoist them once outside their loop, and an indirection there is a
    per-candidate cost — the same argument `inventory_zoning` records for staying a mixin.
    """

    __slots__ = ('sku_sets', 'idx_sets', 'sku_counts', 'member_pos',
                 'demand_sum', 'pick_load_sum', 'vol_sum',
                 'sku_demand_product', 'sku_pick_load_product', 'sku_vol_product')

    def __init__(self) -> None:
        # ── membership ────────────────────────────────────────────────────────────────
        self.sku_sets: dict[int, set[int]] = defaultdict(set)
        self.idx_sets: dict[int, set[int]] = defaultdict(set)
        self.sku_counts: dict[int, dict[int, int]] = defaultdict(dict)
        self.member_pos: dict[int, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        # ── priced levels ─────────────────────────────────────────────────────────────
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

    # ── binding to books somebody else owns ───────────────────────────────────────────

    #: every book this class owns, in the order `__init__` creates them.  `over` refuses
    #: a name that is not here, so a copier table with a stale spelling is a loud failure
    #: rather than a binding that silently does nothing.  The subset a POLICY writes is
    #: `POLICY_BOOKS` below, which is the one the gain evaluator has to cover.
    BOOKS = ('sku_sets', 'idx_sets', 'sku_counts', 'member_pos',
             'demand_sum', 'pick_load_sum', 'vol_sum',
             'sku_demand_product', 'sku_pick_load_product', 'sku_vol_product')

    #: the books a PLACEMENT POLICY writes -- everything `add_sku` and `add_bin` touch.
    #: `sku_counts` is the manager's (`count_bin`) and the three per-SKU products are
    #: read-only to a policy, so neither is here.
    #:
    #: This is the list the inbound gain evaluator must cover.  A virtual placement opens
    #: the arm's real pool, so every book that pool can write has to be a copy-on-write
    #: wrapper or the virtual placement advances the REAL warehouse -- silently, and every
    #: later placement in the run priced against a warehouse that never happened.
    #: `Inbound/` may not import this module (the broker rule), so the DRIVER checks the
    #: two vocabularies against each other at import; see `strategy_runner`.
    POLICY_BOOKS = ('sku_sets', 'idx_sets', 'member_pos',
                    'demand_sum', 'pick_load_sum', 'vol_sum')

    @classmethod
    def over(cls, *, sku_sets=None, idx_sets=None, sku_counts=None, member_pos=None,
             demand_sum=None, pick_load_sum=None, vol_sum=None,
             sku_demand_product=None, sku_pick_load_product=None, sku_vol_product=None,
             **unknown) -> 'AisleLedger':
        """A ledger bound to dicts SOMEBODY ELSE owns, rather than ones it created.

        Two callers, and they are the reason this exists rather than `__init__`:

        - a placement policy, which is handed the aisle dicts down a long parameter
          chain and must write through to whichever set it was given;
        - the inbound gain evaluator, which hands a policy copy-on-write wrappers so a
          VIRTUAL placement cannot advance the real warehouse (`Inbound/gain.py`).

        A book left out reads as empty and REFUSES a write (`_UnboundBook`), so a family
        that never maintains `pick_load_sum` has somewhere harmless to look without a
        `None` check on the hot path, and a family that writes a book nobody gave it
        fails loudly instead of writing where no reader can follow.

        **Spelled out rather than looped, deliberately.**  `**books` plus ten `setattr`s
        cost 2.00 us; this costs 0.29 us, and the difference is 23.4% of a whole pool open
        against 4.2% -- paid once per virtual placement in the gain evaluator, which
        rebuilds the arm's policy every time.  `**unknown` keeps the helpful refusal for a
        misspelled book and is free (0.014 us).
        """
        if unknown:
            raise TypeError(f'AisleLedger.over() got books it does not own: '
                            f'{sorted(unknown)}; it owns {list(cls.BOOKS)}')
        led = cls.__new__(cls)
        led.sku_sets = _UNBOUND if sku_sets is None else sku_sets
        led.idx_sets = _UNBOUND if idx_sets is None else idx_sets
        led.sku_counts = _UNBOUND if sku_counts is None else sku_counts
        led.member_pos = _UNBOUND if member_pos is None else member_pos
        led.demand_sum = _UNBOUND if demand_sum is None else demand_sum
        led.pick_load_sum = _UNBOUND if pick_load_sum is None else pick_load_sum
        led.vol_sum = _UNBOUND if vol_sum is None else vol_sum
        led.sku_demand_product = (_UNBOUND if sku_demand_product is None
                                  else sku_demand_product)
        led.sku_pick_load_product = (_UNBOUND if sku_pick_load_product is None
                                     else sku_pick_load_product)
        led.sku_vol_product = _UNBOUND if sku_vol_product is None else sku_vol_product
        return led

    @property
    def bound(self) -> frozenset:
        """Which books this ledger was HANDED.  Derived, so it cannot disagree with them.

        An owning ledger holds them all; a policy's ledger holds only the ones its family
        maintains, which is how `reconcile` knows which levels it may ask about.
        """
        return frozenset(n for n in self.BOOKS if getattr(self, n) is not _UNBOUND)

    def maintained_levels(self) -> tuple[str, ...]:
        """The priced levels this ledger is entitled to be reconciled on.

        For a policy's ledger these are the levels its family was handed and therefore
        maintains -- `demand_sum` for every family, plus `pick_load_sum` on the two labour
        balancers and `vol_sum` on `rank_cartlabor`.  Derived from the wiring, so it cannot
        disagree with it; hand-listing the same fact a second time is what ticket 03 calls
        `ledger_terms` and what this makes unnecessary.
        """
        return tuple(name for name, _ in self.LEVELS if name in self.bound)

    # ── the add pair ──────────────────────────────────────────────────────────────────
    #
    # THREE methods here against two on the drop side, and the asymmetry is deliberate:
    # the count DECREMENT is what decides "last bin", so it is fused into `drop_sku`,
    # while the increment has nothing to decide and stands alone.
    #
    # These replaced eleven hand-copied commit blocks in `Assignment_Functions.py`, which
    # maintained three different subsets between them.  Two of the three subsets were
    # wrong (see the module docstring), and no assertion could have said so.

    def count_bin(self, aid: int, sku: int) -> None:
        """One more bin of this SKU in this aisle.  The manager's half of a placement.

        `_execute_placement` has committed this and nothing else since before the other
        nine dicts existed — which is exactly how a policy that forgot a sum left the
        COUNT right and the PRICE wrong, and read as a working warehouse.
        """
        counts = self.sku_counts[aid]
        counts[sku] = counts.get(sku, 0) + 1

    def add_bin(self, aid: int, idx: int | None, x_phys: float) -> None:
        """Record ONE bin's column position and its SKU's matrix index.  Mirrors `drop_bin`.

        Per-BIN, so it sits OUTSIDE the `holds()` guard at every call site: `member_pos`
        tracks live bin columns and must see every placement, while the priced levels are
        SKU-once.  Six of the eleven historical blocks already had it on this side of the
        guard and five kept no positions at all; none had it on the wrong side.
        """
        if idx is None:
            return
        self.idx_sets[aid].add(idx)
        self.member_pos[aid][idx].append(x_phys)

    def add_sku(self, aid: int, sku: int, idx: int | None = None, *,
                demand: float | None = None,
                pick_load: float | None = None,
                vol: float | None = None) -> None:
        """FIRST bin of this SKU in this aisle: membership, and the levels it is priced into.

        Per-SKU, so it sits INSIDE the `holds()` guard, which stays at the call site
        because six of the eleven callers do per-bin work on the other side of it.

        **`None` is not 0.0.**  A level is `None` when the calling family does not
        maintain it at all, and is then skipped — not added as zero, which would
        materialise a `defaultdict` key that family never had and change the rows
        `aisle_metrics` later writes.  A family that DOES maintain a level passes its
        value even when that value is 0.0, exactly as the hand-written `+= splp.get(sku,
        0.0)` did.  This is ticket 03's `ledger_terms` in its usable form: the terms a
        family maintains are the keywords it passes, derived at the call site rather
        than hand-listed a second time somewhere else.

        The caller passes the ALREADY-MULTIPLIED scalar (`f_s * q_s`, or an `fq` computed
        at a run boundary) because these are running float sums: re-deriving the product
        here from `f_s` and `q_s` would move the last bits on the families that had
        already hoisted it, and the placement oracles compare exact floats.
        """
        self.sku_sets[aid].add(sku)
        if idx is not None:
            self.idx_sets[aid].add(idx)
        if demand is not None:
            self.demand_sum[aid] += demand
        if pick_load is not None:
            self.pick_load_sum[aid] += pick_load
        if vol is not None:
            self.vol_sum[aid] += vol

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
                 *, last_when_zero: bool = False) -> bool:
        """Decrement the SKU's bin count; on its LAST bin, retire it and subtract its levels.

        Returns True when the SKU left the aisle.  Every subtraction is clamped at zero, as
        the two hand-written copies were.

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

    #: level -> the per-SKU product it is summed from.  `reconcile` walks these.
    LEVELS = (('demand_sum', 'sku_demand_product'),
              ('pick_load_sum', 'sku_pick_load_product'),
              ('vol_sum', 'sku_vol_product'))

    def reconcile(self, *, tol: float = 1e-6) -> list[str]:
        """Every priced level must equal the sum of its members' products.  [] when sound.

        This is the assertion the eleven loose dicts made impossible to write, and the reason
        both drifts survived for the life of their features.  Returns findings rather than
        raising so a caller can report them all at once.

        **Unconditional, and it took two tickets to get there.**  It briefly took a `levels`
        argument, because `init_demand_state` priced all three levels off the current
        placement while a family maintains only the ones it scores on -- so on fifteen of the
        seventeen arms two levels drifted from the first placed unit, and asking the whole
        question would have reported the SEED rather than the warehouse.  Ticket 23 made the
        seed take the arm's terms, so there is no level left that is priced and then
        abandoned, and the argument was deleted with the condition that needed it.  A level
        whose products were never seeded is skipped below, which is a different thing: that
        is a level this run does not have, not one it has and mismaintains.
        """
        out: list[str] = []
        for level_name, product_name in self.LEVELS:
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
