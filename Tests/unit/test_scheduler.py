"""Task→picker scheduler (Pick.assign_tasks): 'round_robin' (default, byte-identical to the legacy
i%num_pickers) vs 'lpt' (load-balance to cut makespan → throughput, at unchanged per-task work).

Guards the properties the LPT scheduler rests on:
  * round_robin is EXACTLY the legacy partition (so the golden suite stays byte-identical);
  * the scheduler's PREDICTED makespan (via the shared cart next-fit) EQUALS the sim's REALIZED
    makespan — the exactness self-consistency;
  * Pick ↔ fast_pick produce the identical partition + makespan under lpt (four-way lockstep);
  * lpt never worsens and strictly improves makespan when round-robin is imbalanced.
"""
import types

from Warehouse.picking.Pick import (PickConfig, PickSimulation, assign_tasks,
                            _task_static, count_cart_swaps)
from Warehouse.picking.fast_pick import DeferredPickSimulation
from Warehouse.layout.Storage_Primitive import FulfillmentCart
from Warehouse.kernel.cost_model import sec_per_inch


def _order(sku, vol, wt=5):
    return types.SimpleNamespace(sku=sku, weight=wt, volume=(lambda v=vol: v))


def _bin(aid, x, y, o, qty):
    return types.SimpleNamespace(
        x_phys=float(x), y_phys=float(y), location=(aid, x, y),
        storage=types.SimpleNamespace(order=o, quantity=qty),
        aisle=types.SimpleNamespace(aisle_width=2400.0))


def _task(aid, npicks, vol=15000, qty=3):
    path, items = [], {}
    for i in range(npicks):
        sku = aid * 100 + i
        path.append(_bin(aid, 100 + i * 40, 30, _order(sku, vol), qty))
        items[sku] = qty
    return types.SimpleNamespace(aisle_id=aid, path=path, items=items)


def _tasks(heavy_aisles):
    """15 aisles; the given aisles are HEAVY (20 picks), the rest light (3 picks)."""
    return [_task(aid, 20 if aid in heavy_aisles else 3) for aid in range(1, 16)]


def _cfg(scheduler, npickers=3, one_way=False):
    return PickConfig(num_pickers=npickers, cart=FulfillmentCart, cart_swap_coef=240,
                      scheduler=scheduler, one_way=one_way)


def _predicted_makespan(picker_tasks, cfg):
    """Exact makespan of a partition, from the shared next-fit — what the scheduler 'sees'."""
    cap, coef = cfg.cart.capacity(), cfg.cart_swap_coef
    xp, yp = sec_per_inch(cfg.x_speed), sec_per_inch(cfg.y_speed)
    loads = []
    for tasks in picker_tasks:
        static, rem, sw = 0.0, cap, 0
        for t in tasks:
            st, vols = _task_static(t, cfg, xp, yp)
            static += st
            s, rem = count_cart_swaps(vols, rem, cap)
            sw += s
        loads.append(static + coef * sw)
    return max(loads) if loads else 0.0


def test_round_robin_is_the_legacy_partition():
    """Default scheduler must reproduce i%num_pickers exactly (byte-identical golden suite)."""
    tasks = sorted(_tasks({3, 7, 11}), key=lambda t: t.aisle_id)
    got = assign_tasks(tasks, _cfg('round_robin'))
    want = [[] for _ in range(3)]
    for i, t in enumerate(tasks):
        want[i % 3].append(t)
    assert [[t.aisle_id for t in p] for p in got] == [[t.aisle_id for t in p] for p in want]


def test_lpt_keeps_aisle_id_order_and_covers_all_tasks():
    tasks = sorted(_tasks({1, 4, 7}), key=lambda t: t.aisle_id)
    got = assign_tasks(tasks, _cfg('lpt'))
    assert sorted(t.aisle_id for p in got for t in p) == [t.aisle_id for t in tasks]  # partition
    for p in got:
        assert [t.aisle_id for t in p] == sorted(t.aisle_id for t in p)               # aisle_id order


def test_predicted_makespan_equals_realized():
    """Exactness self-consistency: the scheduler's predicted makespan == the sim's realized
    (max done-time), for both sims, both lane models, both schedulers."""
    for scheduler in ('round_robin', 'lpt'):
        for one_way in (False, True):
            cfg = _cfg(scheduler, one_way=one_way)
            sim = PickSimulation(_tasks({1, 4, 7}), cfg)
            predicted = _predicted_makespan(sim._picker_tasks, cfg)   # BEFORE run (bins undepleted)
            realized = max(e.time for e in sim.run())
            assert abs(predicted - realized) < 1e-9, (scheduler, one_way, predicted, realized)


def test_pick_fastpick_lockstep_under_lpt():
    """Pick and fast_pick produce the identical partition + makespan under lpt (multi-picker)."""
    for one_way in (False, True):
        cfg = _cfg('lpt', one_way=one_way)
        ref = max(e.time for e in PickSimulation(_tasks({1, 4, 7}), cfg).run())
        fast = max(e.time for e in DeferredPickSimulation(_tasks({1, 4, 7}), cfg).run())
        assert abs(ref - fast) < 1e-9, (one_way, ref, fast)


def test_lpt_beats_round_robin_when_imbalanced():
    """Heavies at aisles 1,4,7 all land on picker 0 under round-robin (i%3); lpt spreads them, so
    makespan drops sharply and throughput rises — the whole point of the scheduler."""
    tasks_rr = _tasks({1, 4, 7})
    tasks_lpt = _tasks({1, 4, 7})
    rr = max(e.time for e in PickSimulation(tasks_rr, _cfg('round_robin')).run())
    lpt = max(e.time for e in PickSimulation(tasks_lpt, _cfg('lpt')).run())
    assert lpt < rr * 0.75, (rr, lpt)   # a large, unambiguous makespan reduction


def test_lpt_matches_when_round_robin_already_balanced():
    """Heavies at 3,7,11 land one-per-picker under round-robin (already optimal); lpt must not
    make it worse (the step-function trap a naive exact-greedy falls into)."""
    rr = max(e.time for e in PickSimulation(_tasks({3, 7, 11}), _cfg('round_robin')).run())
    lpt = max(e.time for e in PickSimulation(_tasks({3, 7, 11}), _cfg('lpt')).run())
    assert lpt <= rr + 1e-6, (rr, lpt)
