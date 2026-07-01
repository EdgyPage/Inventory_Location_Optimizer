"""mkdocs-macros helpers: inject run parameters and formulas into the docs.

Values are pulled from JSON snapshots committed into ``docs/`` (the raw run
outputs live on an external drive and are gitignored, and CI only has the repo).
Each ``config.json`` sits next to its run's images under
``docs/results/images/<run>/<inv>/<cfg>/``; inventory ``params.json`` copies live
under ``docs/inventory/data/<inv>/``.

Formula *shapes* are fixed here from the code that defines them
(``Optimization/run_simulation.py``, ``Warehouse/generation/generate_inventory.py``);
only the *numbers* come from JSON, so pages never hard-code a value twice.

Exception: ``assignment_formulas`` transcribes the top-3 assignment-function
*equations* directly from ``Warehouse/Assignment_Functions.py`` /
``Warehouse/inventory_optimal.py``. Those objectives are not (yet) emitted to any
JSON/DB snapshot, so — unlike every other macro here — there is no programmatic
source to read. A future refactor should expose each builder's objective (e.g. a
``formula`` field on the builder or a small registry) so this macro can read it like
the others; until then the shapes are maintained by hand against the code.
"""

import json
import os

_CACHE = {}


def define_env(env):
    docs_dir = env.conf["docs_dir"]

    # Per-experiment base for the committed JSON snapshots. Each experiment folder is
    # self-contained (its own images/ + data/). A future multi-experiment refactor would
    # parameterise this per call (out of scope now — everything here is Experiment 1).
    _EXPERIMENT = "experiments/experiment-1"

    # ---- loading -------------------------------------------------------------

    def _load_json(rel_path):
        """Load a JSON file relative to the docs dir, cached. Fails loudly so a
        typo'd path breaks `mkdocs build --strict` instead of rendering blank."""
        abs_path = os.path.join(docs_dir, *rel_path.split("/"))
        if abs_path not in _CACHE:
            if not os.path.isfile(abs_path):
                raise FileNotFoundError(
                    f"macros: expected committed JSON at {rel_path} "
                    f"(looked in {abs_path}). Copy the run's config.json/params.json "
                    f"into docs/ — see docs/authoring.md."
                )
            with open(abs_path, encoding="utf-8") as fh:
                _CACHE[abs_path] = json.load(fh)
        return _CACHE[abs_path]

    @env.macro
    def run_config(run, inv, cfg):
        """Return the parsed config.json dict for one run/inventory/config."""
        return _load_json(f"{_EXPERIMENT}/images/{run}/{inv}/{cfg}/config.json")

    @env.macro
    def inv_params(inv):
        """Return the parsed inventory params.json dict for one variant."""
        return _load_json(f"{_EXPERIMENT}/data/{inv}/params.json")

    # ---- number / spec formatting -------------------------------------------

    def _num(x):
        """Trim trailing zeros: 15.0 -> '15', 0.58 -> '0.58', 1.5 -> '1.5'."""
        if isinstance(x, bool) or x is None:
            return str(x)
        if isinstance(x, float) and x.is_integer():
            return str(int(x))
        return f"{x:g}" if isinstance(x, (int, float)) else str(x)

    def _fmt_fn(coef, fn, var):
        """Render a pick-time term like 0.58·w^1.5 or 0.7·log2(v)."""
        kind, _, arg = str(fn).partition(":")
        c = _num(coef)
        if kind == "pow":
            return f"{c}·{var}^{_num(float(arg))}"
        if kind == "log":
            return f"{c}·log{_num(float(arg))}({var})"
        return f"{c}·{kind}({var})"

    def _fmt_spec(spec):
        """One-line summary of a dimension/size distribution spec."""
        dist = spec.get("dist")
        if dist == "triangular":
            return f"tri({_num(spec['low'])}–{_num(spec['high'])}, mode {_num(spec['mode'])})"
        if dist == "normal":
            return f"norm(μ{_num(spec['mean'])}, σ{_num(spec['std'])})"
        if dist == "uniform":
            return f"U({_num(spec['low'])}–{_num(spec['high'])})"
        if dist == "mixture":
            parts = [
                f"{_num(c['prob'])}·{_fmt_spec(c['spec'])}" for c in spec["components"]
            ]
            return "mix(" + " + ".join(parts) + ")"
        return dist or "?"

    def _fmt_weight(spec):
        dist = spec.get("dist")
        if dist == "volume_poisson":
            return "∝ volume (Poisson)"
        if dist == "volume_scaled_poisson":
            return f"∝ volume ×{_num(spec['scale'])} (Poisson)"
        if dist == "poisson_fixed":
            return f"Poisson(λ{_num(spec['lam'])})"
        return _fmt_spec(spec)

    # ---- rendered blocks -----------------------------------------------------

    @env.macro
    def setup_table(run, inv, cfg):
        """Markdown table of the headline simulation setup for a run/inv/config."""
        c = run_config(run, inv, cfg)
        rows = [
            ("SKUs (placed / units)", f"{c['n_skus']:,} / {c['total_units']:,}"),
            ("Warehouse", f"{c['total_aisles']:,} aisles · {c['total_bins']:,} bins · {_num(c['bin_slack_pct'])}% slack"),
            ("Pickers", _num(c["num_pickers"])),
            ("Batches", _num(c["n_batches"])),
            ("Seeds (world / batches)", f"{_num(c['seed_world'])} / {_num(c['seed_batches'])}"),
            ("Equilibrium qty (avg)", _num(c["avg_equilibrium_qty"])),
            ("Reorder point (avg)", _num(c["avg_reorder_point"])),
            ("Lead time (avg batches)", _num(c["avg_lead_time_mean"])),
            ("Supply CV (avg)", _num(c["avg_supply_cv"])),
        ]
        lines = ["| Parameter | Value |", "|-----------|-------|"]
        lines += [f"| {k} | {v} |" for k, v in rows]
        return "\n".join(lines)

    @env.macro
    def pick_time_formula(run, inv, cfg):
        """Pick-time cost model with this config's calibrated coefficients."""
        c = run_config(run, inv, cfg)
        w = _fmt_fn(c["pick_weight_coef"], c["pick_weight_fn"], "w")
        v = _fmt_fn(c["pick_volume_coef"], c["pick_volume_fn"], "v")
        return (
            f"`t_pick = {_num(c['pick_intercept'])} + {w} + {v}`  seconds per task, "
            f"for item weight *w* (lb) and volume *v* (in³); "
            f"cart swap **{_num(c['cart_swap_coef'])} s**, travel speeds "
            f"**{_num(c['x_speed'])} / {_num(c['y_speed'])} ft·s⁻¹** (cross-aisle / along-aisle). "
            f"Height brackets add an ergonomic multiplier in the `high_height` variants."
        )

    @env.macro
    def assignment_formulas():
        """Equations of the top-3 winning assignment functions (Rank_labor, Map,
        Map_rank), transcribed from the source that defines them. See the module
        docstring: this is the one macro whose *formula* is hand-maintained against
        code because no JSON/DB snapshot emits these objectives yet.

        Sources: `_travel_balanced_impl` / `build_optmap_fn` in
        Warehouse/Assignment_Functions.py; `build_optimal_map` in
        Warehouse/inventory_optimal.py; registry in Optimization/strategies.py.
        """
        return "\n".join([
            "All three share one **per-bin labor primitive** — the expected time to make one "
            "pick at bin *b*:",
            "",
            "`ℓ(b) = M(y_b)·(t₀ + v) + D_b`   with   `D_b = x_pace·x_phys + y_pace·y_phys`",
            "",
            "where *t₀* is the pick intercept, *v* the per-pick handling term "
            "(`handle_var`), *M(y_b)* the height-bracket multiplier, and *D_b* the "
            "entrance-relative travel cost (low *D* = front bay). `x_pace`/`y_pace` are the "
            "per-inch paces `sec_per_inch(x_speed)` / `sec_per_inch(y_speed)`.",
            "",
            "**1. Rank_labor** — travel-aware LPT (longest-processing-time) labor balance. "
            "Aisle *a*'s total expected labor is `L_a = Σ_{s∈a} f_s·q_s·ℓ(b_s)`; each unit is "
            "placed in the `(aisle, bin)` that minimises `L_a + f_s·q_s·ℓ(b)` (least raises the "
            "busiest aisle), costliest SKU first. *f_s* = relative pick frequency (a [0,1] "
            "selection share), *q_s* = pick quantity.",
            "",
            "**2. Map** — optimal-map score matching. Each bin has a quantity-free preferred "
            "score `pref(b) = D_b + M(y_b)·(t₀ + v̄)` (*v̄* = mean handling term); each SKU has "
            "a target `target(s) =` the `pref` of its bin in the labor-minimising full "
            "linear assignment problem (LAP). A unit is placed at `argmin_b |pref(b) − target(s)|`.",
            "",
            "**3. Map_rank** — the same map, upgrade-capped: a SKU never reloads into a bin "
            "more prime than its optimal rank. Place at "
            "`argmin_{b : pref(b) ≥ target(s)} (pref(b) − target(s))`; if no bin is at or below "
            "the SKU's tier, fall back to the least-prime free bin.",
        ])

    @env.macro
    def reorder_formula(run, inv, cfg):
        """Equilibrium / reorder-point model with this config's averages."""
        c = run_config(run, inv, cfg)
        return (
            "`q_eq = round(coverage × d̄)` and "
            "`ROP = round(d̄ × (lead + safety))`, "
            "where *d̄* is a SKU's expected per-batch demand. This run's averages: "
            f"equilibrium **{_num(c['avg_equilibrium_qty'])}**, "
            f"reorder point **{_num(c['avg_reorder_point'])}**, "
            f"lead time **{_num(c['avg_lead_time_mean'])}** batches, "
            f"supply CV **{_num(c['avg_supply_cv'])}**."
        )

    @env.macro
    def inv_distribution_table(inv):
        """Per-category distribution table for an inventory variant's params.json."""
        p = inv_params(inv)
        header = (
            "| Category | Share | Length (in) | Width (in) | Height (in) "
            "| Weight | Handling (conv/non) | Freq | Qty |\n"
            "|----------|------:|-------------|------------|-------------"
            "|--------|--------------------|------|-----|"
        )
        lines = [header]
        for e in p["creation_plan"]:
            conv, non = e["handling_split"]
            lines.append(
                f"| {e['category']} | {_num(e['share'] * 100)}% "
                f"| {_fmt_spec(e['length_spec'])} "
                f"| {_fmt_spec(e['width_spec'])} "
                f"| {_fmt_spec(e['height_spec'])} "
                f"| {_fmt_weight(e['weight_spec'])} "
                f"| {_num(conv * 100)}% / {_num(non * 100)}% "
                f"| {_fmt_spec(e['freq_spec'])} "
                f"| {_fmt_spec(e['qty_spec'])} |"
            )
        return "\n".join(lines)

    # config variant -> short ergonomics label
    _CONFIGS = [
        ("calibrated", "base ergonomics"),
        ("calibrated_high_weight", "weight penalty ↑"),
        ("calibrated_high_height", "height penalty ↑"),
        ("calibrated_high_weight_high_height", "weight + height penalty ↑"),
    ]
    # curated figures shown per config (filename -> caption)
    _FIGURES = [
        ("top3_by_initial_prodtime_cum_improvement.png",
         "Cumulative production-time improvement vs FIFO — top 3 initial-placement strategies."),
        ("top3_by_initial_production_time_over_time.png",
         "Production time per batch over the run."),
        ("top3_by_initial_prodtime_delta_trend.png",
         "Production-time delta vs FIFO — smoothed trend."),
        ("top_vs_baseline_table.png",
         "Top strategies vs FIFO baseline — significance table (means, deltas, p-values)."),
        ("top_vs_baseline.png",
         "Top strategies vs FIFO baseline — effect sizes with confidence intervals."),
    ]

    @env.macro
    def run_section(run, inv):
        """Collapsible per-config figure blocks for one run/inventory variant."""
        out = []
        for cfg, label in _CONFIGS:
            out.append(f'??? note "{cfg} — {label}"')
            for fname, caption in _FIGURES:
                path = f"images/{run}/{inv}/{cfg}/{fname}"
                out.append(f"    ![{caption}]({path}){{ width=820 }}")
                out.append("")
                out.append(f"    *{caption}*")
                out.append("")
        return "\n".join(out).rstrip()

    # full assignment-function suite figures (every strategy arm), for the compiled
    # Full-results report — filename -> caption.
    _FULL_SUITE_FIGURES = [
        ("task_duration_by_strategy.png",
         "Steady-state task duration for every strategy arm (Uni|… and Opt|… × 16 families); "
         "diamond = mean. The full suite, ranked."),
        ("production_time_over_time.png",
         "Production time per batch, all 16 assignment functions overlaid "
         "(Opt = solid, Uni = dashed)."),
    ]

    @env.macro
    def full_suite_section(run, inv):
        """Collapsible per-config full-suite figure blocks (all strategies) for one
        run/inventory variant — used by the compiled Full-results report."""
        out = []
        for cfg, label in _CONFIGS:
            out.append(f'??? note "{cfg} — {label}"')
            for fname, caption in _FULL_SUITE_FIGURES:
                path = f"images/{run}/{inv}/{cfg}/{fname}"
                out.append(f"    ![{caption}]({path}){{ width=820 }}")
                out.append("")
                out.append(f"    *{caption}*")
                out.append("")
        return "\n".join(out).rstrip()

    @env.macro
    def inv_lead_time(inv):
        """Short human description of a variant's replenishment lead time."""
        p = inv_params(inv)
        rng = p.get("lead_time_range")
        if not rng:
            return "immediate (0 batches)"
        return f"uniform {_num(rng[0])}–{_num(rng[1])} batches"
