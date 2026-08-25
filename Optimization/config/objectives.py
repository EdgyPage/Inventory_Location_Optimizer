"""objectives.py — what each restock rule OPTIMISES, in one machine-readable place.

WHY THIS EXISTS.  The objective of a placement rule used to live in three places that
drifted independently: the Python closure that implements it, a hand-written LaTeX table
in `docs/macros.py::assignment_formulas` (top-3 only), and a second hand-written copy in
each experiment's `formula-reference.md` (all 17).  The macro's own docstring named the
fix — "expose each builder's objective … so this macro can read it like the others" —
and this module is it.  The catalog is emitted INTO each run, so the objective published
beside a result is the objective that ran, versioned with it.

WHAT THIS CANNOT DO, stated plainly: the LaTeX is not derived from the Python.  No
inspection recovers an objective from a closure.  What the registry buys is that the
transcription exists exactly ONCE and is machine-anchored — `Tests/architecture/
test_rule_catalog.py` asserts every rule has an entry, every named symbol exists, and
every builder actually calls the symbol its entry claims.  A rename or a re-pointed
builder fails a test instead of silently making a published formula a lie.

KEYED BY RESTOCK KEY, not by arm.  `Strategy.restock` already carries it, so the tie is
`OBJECTIVES[s.restock]` and `Optimization/config/strategies.py` needs no edit — which
also keeps LaTeX out of the strategy grid's import path and keeps that file (a
run-contract SHAPE_SOURCE) out of unrelated diffs.

The shared machinery each objective is written against — the placement primitive
`ell(b) = M(y_b)(t0 + h) + D_b`, the pick-effort priority that orders a ranked wave, and
the tie-breaking / full-aisle rules — is documented once on the experiment site's
formula reference; the entries below state only what distinguishes them.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

_AF = 'Warehouse/placement/Assignment_Functions.py'
_STRAT = 'Optimization/config/strategies.py'


# ── the descriptor ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Objective:
    """One restock rule's identity: what it is called, what code it is, what it optimises.

    rule       restock key, matching `Strategy.restock` and `_RESTOCKS[i][0]`
    label      display label, matching `_RESTOCKS[i][1]`
    family     which bracket it belongs to (see `FAMILIES` below)
    sense      'min' | 'max' | 'none' — the direction of its argument
    stage      'per_unit' | 'ranked_wave' | 'precomputed_map' — WHEN it decides
    symbol     the placement function the builder actually calls
    module     repo-relative file that DEFINES `symbol`
    builder    the `_build_*` in the strategy grid that wires it up
    precompute an offline build step run once per arm before the sim, or '' for none.
               This is the field a WMS reader needs: a rule with a precompute has a job
               to schedule; a rule without one is pure per-arrival scoring.
    latex      the objective, one expression, `$…$`-delimited
    notes      the mechanism prose that belongs with the formula rather than the page
    control    True for the deliberate worst-case bracket controls (designed to lose)
    """
    rule       : str
    label      : str
    family     : str
    sense      : str
    stage      : str
    symbol     : str
    module     : str
    builder    : str
    latex      : str
    notes      : str
    control    : bool = False
    precompute : str = ''

    @property
    def anchor(self) -> str:
        """The heading anchor the published rule section uses.

        Load-bearing: `full-results.md` and `glossary.md` link to these fragments and
        `context/guards/docref_guard.py` checks them, so the generated section headings
        must keep reproducing exactly this string.
        """
        return self.rule.replace('_', '-')


#: family key -> the section heading that groups its rules on a published page
FAMILIES = {
    'baseline':    'Baseline',
    'ranked':      'Ranked (effort / labor)',
    'map':         'Map (optimal-map score matching)',
    'cluster_map': 'Cluster-map (map + cohesion)',
    'travel':      'Travel bracket',
    'affinity':    'Affinity bracket',
    'codemand':    'Co-demand bracket',
}

#: The offline map solve shared by the four map-family rules.  Named once so the
#: catalog's `precompute` field cannot drift from the code it points at.
_MAP_BUILD = 'build_optimal_map@Warehouse/inventory/inventory_optimal.py'


# ── the 17 rules ────────────────────────────────────────────────────────────────

_ENTRIES = [
    Objective(
        rule='fifo', label='FIFO', family='baseline', sense='none', stage='per_unit',
        symbol='_uniform_assignment', module='Warehouse/inventory/inventory_common.py',
        builder=f'_build_uniform@{_STRAT}',
        latex='',
        notes='First-in-first-out: drop each arriving unit into a uniform-random bin of '
              'its BinKey pool. No ordering, no affinity, no demand awareness — the '
              'do-nothing control every other family is measured against. It has no '
              'objective, which is the point.',
    ),

    # ── ranked (effort / labor) ─────────────────────────────────────────────────
    Objective(
        rule='rank_random', label='Rank_random', family='ranked', sense='none',
        stage='ranked_wave', symbol='build_ranked_uniform_pool_fn', module=_AF,
        builder=f'_build_uniform_trip_min_ranked@{_STRAT}',
        latex='',
        notes='Rank by pick-effort priority, then place each unit in a uniform-random '
              'aisle at its lowest-$D$ (front) bin. Isolates the *ordering* effect from '
              'the *placement* effect — how much of a win is just sequencing hot units '
              'first.',
    ),
    Objective(
        rule='rank_popularity', label='Rank_popularity', family='ranked', sense='min',
        stage='ranked_wave', symbol='build_ranked_popularity_pool_fn', module=_AF,
        builder=f'_build_rank_popularity@{_STRAT}',
        latex=r'$\arg\min_{a}\ \sum_{s \in a} f_s\,q_s$',
        notes='Rank by expected popularity ($f\\cdot q$), place each into the aisle with '
              'the least total popularity. Spreads demand mass evenly across aisles — a '
              'dispersal control.',
    ),
    Objective(
        rule='rank_labor', label='Rank_labor', family='ranked', sense='min',
        stage='ranked_wave', symbol='build_ranked_labor_pool_fn', module=_AF,
        builder=f'_build_rank_labor@{_STRAT}',
        latex=r'$\arg\min_{(a,\,b)}\ \bigl(L_a + f_s\,q_s\,\ell(b)\bigr),'
              r'\qquad L_a = \sum_{s\in a} f_s\,q_s\,\ell(b_s)$',
        notes='Travel-aware LPT (longest-processing-time) labor balance. Aisle $a$ '
              'carries total expected labor $L_a$; each unit is placed where it least '
              'raises the busiest aisle, costliest SKU first.',
    ),
    Objective(
        rule='rank_cartlabor', label='Rank_cartlabor', family='ranked', sense='min',
        stage='ranked_wave', symbol='build_ranked_cartlabor_pool_fn', module=_AF,
        builder=f'_build_rank_cartlabor@{_STRAT}',
        latex=r'$C_a = c_{\text{swap}} \cdot \max\!\left(0,\ \frac{V_a}{\hat{V}} - 1\right),'
              r'\qquad \arg\min_{(a,\,b)}\ \bigl(L_a + C_a + f_s\,q_s\,\ell(b)\bigr)$',
        notes='Rank_labor plus a cart-swap term: the load being balanced also carries '
              "each aisle's *expected cart-swap* cost, so demand mass that would overflow "
              "a picker's cart disperses rather than concentrating. $V_a = \\sum_{s\\in a} "
              'f_s q_s v_s$ is the raw expected picked volume and $\\hat{V}$ the cart '
              'capacity rescaled to the same units. With a large cart $C_a \\approx 0$ and '
              'this is effectively Rank_labor; with a small cart the term bites. Setting '
              'the cart tuple to `None` makes it byte-identical to Rank_labor.',
    ),
    Objective(
        rule='rank_minlabor', label='Rank_minlabor', family='ranked', sense='min',
        stage='ranked_wave', symbol='build_ranked_minlabor_pool_fn', module=_AF,
        builder=f'_build_rank_minlabor@{_STRAT}',
        latex=r'$\arg\min_{(a,\,b)}\ \Bigl[\,f_s\bigl(M(y_b)(t_0 + h) + D_b\bigr)'
              r'\;-\; \lambda\!\!\sum_{p\,\in\,\text{aisle}}\!\!'
              r'\bigl(\text{lift}(s,p)-1\bigr) f_p\,\Bigr]$',
        notes='Greedy minimiser of expected total task labor. Fuses golden-zone height, '
              'effort-to-front and affinity compaction into one marginal-cost score — it '
              'CONSOLIDATES rather than balances, which is the opposite of Rank_labor.',
    ),
    Objective(
        rule='rank_maxlabor', label='Rank_maxlabor', family='ranked', sense='max',
        stage='ranked_wave', symbol='build_ranked_maxlabor_pool_fn', module=_AF,
        builder=f'_build_rank_maxlabor@{_STRAT}', control=True,
        latex=r'$\arg\max_{(a,\,b)}\ \Bigl[\,f_s\bigl(M(y_b)(t_0 + h) + D_b\bigr)'
              r'\;-\; \lambda\!\!\sum_{p\,\in\,\text{aisle}}\!\!'
              r'\bigl(\text{lift}(s,p)-1\bigr) f_p\,\Bigr]$',
        notes='The exact maximiser mirror of Rank_minlabor — high/far bins, scattered '
              'partners. A worst-case control that should land *worst* on task labor.',
    ),

    # ── map (optimal-map score matching) ────────────────────────────────────────
    Objective(
        rule='map', label='Map', family='map', sense='min', stage='precomputed_map',
        symbol='build_optmap_fn', module=_AF, builder=f'_build_map@{_STRAT}',
        precompute=_MAP_BUILD,
        latex=r'$\arg\min_{b}\ \bigl|\operatorname{pref}(b) - \operatorname{target}(s)\bigr|,'
              r'\qquad \operatorname{pref}(b) = D_b + M(y_b)(t_0 + \bar h)$',
        notes='Optimal-map score matching. Every bin gets a quantity-free preferred score '
              "$\\operatorname{pref}(b)$; every SKU gets a $\\operatorname{target}(s)$ — the "
              "$\\operatorname{pref}$ of its bin in the labor-minimising assignment solved "
              'offline at setup. Arriving units go to the free bin whose score is closest '
              'to their target. The assignment is solved ONCE per arm at setup, from the '
              'demand model, and is not re-solved as realized demand drifts — as a WMS job '
              'that is a periodic offline re-slot computation, not a live service.',
    ),
    Objective(
        rule='map_rank', label='Map_rank', family='map', sense='min',
        stage='precomputed_map', symbol='build_optmap_fn', module=_AF,
        builder=f'_build_map_rank@{_STRAT}', precompute=_MAP_BUILD,
        latex=r'$\arg\min_{\,b\,:\,\operatorname{pref}(b)\,\ge\,\operatorname{target}(s)}\ '
              r'\bigl(\operatorname{pref}(b) - \operatorname{target}(s)\bigr)$',
        notes='The same map, upgrade-capped: a SKU never reloads into a bin more prime '
              'than its optimal rank, reserving prime spots for the higher-ranked SKUs '
              'future orders bring. Rank-relative, and deliberately non-greedy.',
    ),

    # ── cluster-map (map + cohesion) ────────────────────────────────────────────
    Objective(
        rule='cluster_map', label='CluMap', family='cluster_map', sense='min',
        stage='precomputed_map', symbol='build_cluster_map_placement', module=_AF,
        builder=f'_build_cluster_map@{_STRAT}', precompute=_MAP_BUILD,
        latex=r'$\arg\max_{a}\ \text{co-occur}(s,a)\ \ \text{then}\ \ '
              r'\arg\min_{b\,\in\,a}\ \lvert x_b - c_x \rvert$',
        notes='Mix `map` with clustering: choose the aisle cohesion-first (most '
              'demand-weighted affinity to existing members), anchor the unit at its '
              "favoured map location, then compact it toward the partners' column "
              'centroid. Uncapped score-match, so it may upgrade into prime bins.',
    ),
    Objective(
        rule='cluster_map_rank', label='CluMapRk', family='cluster_map', sense='min',
        stage='precomputed_map', symbol='build_cluster_map_placement', module=_AF,
        builder=f'_build_cluster_map_rank@{_STRAT}', precompute=_MAP_BUILD,
        latex=r'$\arg\max_{a}\ \text{co-occur}(s,a)\ \ \text{then}\ \ '
              r'\arg\min_{b\,\in\,a\,:\,\operatorname{pref}(b)\,\ge\,\operatorname{target}(s)}\ '
              r'\lvert x_b - c_x \rvert$',
        notes='Upgrade-capped `cluster_map` — the same cohesion and compaction, but it '
              'never settles more prime than its map target.',
    ),

    # ── travel bracket ──────────────────────────────────────────────────────────
    Objective(
        rule='tmin', label='TripMin', family='travel', sense='min', stage='ranked_wave',
        symbol='build_ranked_minimizing_pool_fn', module=_AF,
        builder=f'_build_trip_min@{_STRAT}',
        latex=r'$\arg\min_{b}\ \bigl(f_s\,D_b - \beta\,\text{co-occur}\bigr)$',
        notes='Hot SKUs to low-$D$ (front) bins, so there is less within-aisle walking.',
    ),
    Objective(
        rule='tmax', label='TripMax', family='travel', sense='max', stage='ranked_wave',
        symbol='build_ranked_maximizing_pool_fn', module=_AF,
        builder=f'_build_trip_max@{_STRAT}', control=True,
        latex=r'$\arg\max_{b}\ \bigl(f_s\,D_b - \beta\,\text{co-occur}\bigr)$',
        notes='Hot items to the back. Worst-case travel control; brackets `tmin`.',
    ),

    # ── affinity bracket ────────────────────────────────────────────────────────
    Objective(
        rule='cmax', label='MaxClu', family='affinity', sense='max', stage='per_unit',
        symbol='build_cluster_maximizing_assignment_fn', module=_AF,
        builder=f'_build_max_cluster@{_STRAT}',
        latex=r'$\arg\max_{a}\ \sum_{p\,\in\,a} \bigl(\text{lift}(s,p) - 1\bigr) f_p$',
        notes='Send each SKU to the aisle where its co-picked partners already sit, so a '
              'batch visits fewer aisles.',
    ),
    Objective(
        rule='cmin', label='MinClu', family='affinity', sense='min', stage='per_unit',
        symbol='build_cluster_minimizing_assignment_fn', module=_AF,
        builder=f'_build_min_cluster@{_STRAT}', control=True,
        latex=r'$\arg\min_{a}\ \sum_{p\,\in\,a} \bigl(\text{lift}(s,p) - 1\bigr) f_p$',
        notes='Scatter partners across aisles. Anti-affinity control; brackets `cmax`.',
    ),

    # ── co-demand bracket ───────────────────────────────────────────────────────
    Objective(
        rule='comp', label='Compact', family='codemand', sense='min', stage='ranked_wave',
        symbol='build_co_demand_placement', module=_AF,
        builder=f'_build_compaction@{_STRAT}',
        latex=r'$\arg\min_{b}\ \lvert x_b - c_x \rvert,\qquad c_x = '
              r'\frac{\sum_p (\text{lift}(s,p)-1)\,f_p\,x_p}'
              r'{\sum_p (\text{lift}(s,p)-1)\,f_p}$',
        notes='Minimise within-aisle span: place the SKU in the column nearest the '
              'demand-weighted centroid of its co-demanded partners already in that '
              'aisle, shortening the sweep path.',
    ),
    Objective(
        rule='expn', label='Expand', family='codemand', sense='max', stage='ranked_wave',
        symbol='build_co_demand_placement', module=_AF,
        builder=f'_build_expansion@{_STRAT}', control=True,
        latex=r'$\arg\max_{b}\ \lvert x_b - c_x \rvert$',
        notes='Maximise within-aisle span — farthest from the centroid. Counter control; '
              'the `comp` to `expn` gap measures what the co-demand lever is worth.',
    ),
]

#: restock key -> Objective.  The registry the run catalog and the site both read.
OBJECTIVES: dict[str, Objective] = {o.rule: o for o in _ENTRIES}


def objective_for(strategy) -> Objective:
    """The Objective behind a `Strategy` (or a bare restock key)."""
    key = getattr(strategy, 'restock', strategy)
    return OBJECTIVES[key]


def as_dicts() -> list:
    """The registry as plain dicts, in declaration order — the emitted catalog's body."""
    return [dict(asdict(o), anchor=o.anchor, family_label=FAMILIES[o.family])
            for o in _ENTRIES]
