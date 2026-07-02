"""mkdocs-macros helpers: inject run parameters and formulas into the docs.

Values are pulled from JSON snapshots committed per experiment under
``docs/experiments/<exp>/`` (raw run outputs live on an external drive, gitignored;
CI only has the repo). Each ``config.json`` sits next to its run's images under
``images/<run>/<inv>/<cfg>/``; inventory ``params.json`` under ``data/<inv>/``.

Two modes, decided per page by its experiment folder:
  * **manifest mode** — the experiment has an ``experiment.yml`` (single source of
    truth for its run id, inventory keys→ids, configs, figures). Pages call macros with
    a short inventory key, e.g. ``setup_table('lt0')``; run/config/figures resolve from
    the manifest. This is how new experiments (2+) are authored. See the ingest script
    ``docs/experiments/ingest.py`` and ``scripts/new_experiment.py``.
  * **legacy mode** — no ``experiment.yml`` (Experiment 1). Pages pass explicit
    ``(run, inv, cfg)`` and the module defaults (``_CONFIGS`` etc.) apply. Unchanged.

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

import yaml

_CACHE = {}


def define_env(env):
    docs_dir = env.conf["docs_dir"]

    # ---- experiment resolution ----------------------------------------------
    # A page's experiment is derived from its path (experiments/<exp>/…). If that
    # folder has an experiment.yml the macros run in **manifest mode** — pages pass a
    # short inventory key and the run/config/figures come from the manifest. With no
    # manifest (e.g. Experiment 1) they stay in **legacy mode** — pages pass explicit
    # (run, inv, cfg) exactly as before, so nothing on those pages changes.

    def _exp_dir():
        page = getattr(env, "page", None)
        src = (getattr(getattr(page, "file", None), "src_path", "") or "").replace("\\", "/")
        parts = src.split("/")
        if len(parts) >= 2 and parts[0] == "experiments":
            return f"experiments/{parts[1]}"
        return "experiments/experiment-1"   # safe default (legacy single experiment)

    def _load_yaml(rel_path):
        abs_path = os.path.join(docs_dir, *rel_path.split("/"))
        if abs_path not in _CACHE:
            with open(abs_path, encoding="utf-8") as fh:
                _CACHE[abs_path] = yaml.safe_load(fh)
        return _CACHE[abs_path]

    def _manifest():
        """The parsed experiment.yml for the current page's experiment, or None (legacy)."""
        rel = f"{_exp_dir()}/experiment.yml"
        abs_path = os.path.join(docs_dir, *rel.split("/"))
        return _load_yaml(rel) if os.path.isfile(abs_path) else None

    # Argument resolvers: turn a macro's args into full (run, inv, cfg) IDs, honouring
    # legacy explicit args (no manifest) or manifest short-keys (manifest present).
    def _ric(args):
        """-> (run, inv, cfg).  legacy: (run, inv, cfg);  manifest: (inv_key[, cfg])."""
        m = _manifest()
        if m is None:
            run, inv, cfg = args
        else:
            inv = m["inventories"][args[0]]["id"]
            cfg = args[1] if len(args) > 1 else m["configs"][0]["name"]
            run = m["run"]
        return run, inv, cfg

    def _ri(args):
        """-> (run, inv).  legacy: (run, inv);  manifest: (inv_key,)."""
        m = _manifest()
        if m is None:
            return args[0], args[1]
        return m["run"], m["inventories"][args[0]]["id"]

    def _inv1(args):
        """-> inv id.  legacy: (inv_id,);  manifest: (inv_key,)."""
        m = _manifest()
        return m["inventories"][args[0]]["id"] if m else args[0]

    def _cfg_list():
        """[(cfg_name, label)] from the manifest, else the module default."""
        m = _manifest()
        return [(c["name"], c.get("label", "")) for c in m["configs"]] if m else _CONFIGS

    def _hbrackets(cfg):
        """Height-bracket rows for a config: manifest first, else the code-sourced map."""
        m = _manifest()
        if m:
            for c in m["configs"]:
                if c["name"] == cfg:
                    return c.get("height_brackets")
        return _HEIGHT_BRACKETS.get(cfg)

    def _figs(kind):
        """[(filename, caption)] for kind in {'top3','full_suite'}: manifest names
        (captions looked up from the module maps) else the module default list."""
        m = _manifest()
        if m:
            caps = dict(_FIGURES + _FULL_SUITE_FIGURES)
            return [(n, caps.get(n, n)) for n in m.get("figures", {}).get(kind, [])]
        return _FIGURES if kind == "top3" else _FULL_SUITE_FIGURES

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

    def _load_cfg(run, inv, cfg):
        return _load_json(f"{_exp_dir()}/images/{run}/{inv}/{cfg}/config.json")

    def _load_params(inv):
        return _load_json(f"{_exp_dir()}/data/{inv}/params.json")

    @env.macro
    def run_config(*args):
        """Parsed config.json — legacy (run,inv,cfg) or manifest (inv_key[,cfg])."""
        return _load_cfg(*_ric(args))

    @env.macro
    def inv_params(*args):
        """Parsed inventory params.json — legacy (inv_id) or manifest (inv_key)."""
        return _load_params(_inv1(args))

    @env.macro
    def experiment():
        """The current page's experiment manifest (dict), or {} in legacy mode. Lets
        manifest-driven template pages loop, e.g.
        ``{% for k, inv in experiment().inventories.items() %}``."""
        return _manifest() or {}

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

    def _fmt_fn_tex(coef, fn, var):
        r"""LaTeX form of a pick-time term: pow:1.5 -> ``0.58\,w^{1.5}``,
        log:2 -> ``0.7\,\log_{2} V``."""
        kind, _, arg = str(fn).partition(":")
        c = _num(coef)
        if kind == "pow":
            return rf"{c}\,{var}^{{{_num(float(arg))}}}"
        if kind == "log":
            base = rf"_{{{_num(float(arg))}}}" if arg else ""
            return rf"{c}\,\log{base} {var}"
        return rf"{c}\,\mathrm{{{kind}}}({var})"

    # Height-bracket multipliers M(y) per calibration. NOT in the committed config.json,
    # so transcribed from Optimization/run_simulation.py REGRESSION_CONFIGS (an exception,
    # like assignment_formulas). Each entry: (upper_y_phys_inches | None, multiplier).
    # TODO(refactor): emit height_brackets into config.json so this becomes programmatic.
    _HEIGHT_BRACKETS = {
        "calibrated":                         [(96, 1.0), (240, 1.2), (None, 1.4)],
        "calibrated_high_weight":             [(96, 1.0), (240, 1.2), (None, 1.4)],
        "calibrated_high_height":             [(96, 1.0), (240, 1.4), (None, 1.8)],
        "calibrated_high_weight_high_height": [(96, 1.0), (240, 1.4), (None, 1.8)],
    }

    def _fmt_brackets(cfg):
        """Human-legible M(y) brackets for a calibration, e.g. '×1.0 (y<96″) · …'."""
        (e1, m1), (e2, m2), (_, m3) = _hbrackets(cfg)
        return (f"×{_num(m1)} (y&lt;{e1}″) · ×{_num(m2)} ({e1}–{e2}″) · "
                f"×{_num(m3)} (&gt;{e2}″)")

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
    def setup_table(*args):
        """Markdown table of the headline simulation setup for a run/inv/config."""
        c = _load_cfg(*_ric(args))
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
    def pick_time_formula(*args):
        """Pick-time cost model (LaTeX) with this config's calibrated coefficients.
        Matches Warehouse/Pick.py: t_pick = M(y)·(t0 + q·h) + c_cart·1[cart swap]."""
        c = _load_cfg(*_ric(args))
        t0 = _num(c["pick_intercept"])
        wt = _fmt_fn_tex(c["pick_weight_coef"], c["pick_weight_fn"], "w")
        vt = _fmt_fn_tex(c["pick_volume_coef"], c["pick_volume_fn"], "V")
        cart = _num(c["cart_swap_coef"])
        vx, vy = c["x_speed"], c["y_speed"]
        dx, dy = _num(12 * vx), _num(12 * vy)
        return "\n".join([
            r"$$t_{\text{pick}} \;=\; M(y)\,\bigl(" + t0 + r" + q\,h\bigr)"
            r" \;+\; " + cart + r"\,\mathbb{1}[\text{cart swap}],"
            r"\qquad h \;=\; " + wt + " + " + vt + r"$$",
            "",
            r"$$D \;=\; \frac{x_{\text{phys}}}{" + dx + r"} + \frac{y_{\text{phys}}}{" + dy +
            r"}\ \text{s}\qquad(\text{speeds } " + _num(vx) + "/" + _num(vy) +
            r"\ \text{ft·s}^{-1}\text{, cross-aisle / along-aisle}).$$",
            "",
            r"Here $t_0$ is the fixed pick setup (s), $q$ the quantity picked, $w$ the item "
            r"weight (lb), $V$ its volume (in³), $y$ the shelf height, $M(y)$ the height-bracket "
            r"multiplier (per calibration below), $h$ the per-pick **handling term**, "
            r"$c_{\text{cart}}$ the cart-swap penalty, and $\mathbb{1}[\cdot]$ its indicator. "
            r"$M(y)$ scales the whole at-location pick; the cart penalty is not height-scaled.",
        ])

    @env.macro
    def pick_calibration_table(*args):
        """One row per calibration: the weight/volume handling terms (from each config's
        JSON) and the height M(y) brackets (manifest, else code-sourced map)."""
        run, inv = _ri(args)
        lines = [
            r"| Calibration | Weight term | Volume term | Height $M(y)$ |",
            "|-------------|-------------|-------------|---------------|",
        ]
        for cfg, label in _cfg_list():
            c = _load_cfg(run, inv, cfg)
            wt = _fmt_fn_tex(c["pick_weight_coef"], c["pick_weight_fn"], "w")
            vt = _fmt_fn_tex(c["pick_volume_coef"], c["pick_volume_fn"], "V")
            lines.append(f"| `{cfg}`<br><small>{label}</small> | ${wt}$ | ${vt}$ "
                         f"| {_fmt_brackets(cfg)} |")
        return "\n".join(lines)

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
            "pick at bin $b$:",
            "",
            r"$$\ell(b) \;=\; M(y_b)\,(t_0 + h) + D_b,\qquad "
            r"D_b \;=\; x_{\text{pace}}\,x_{\text{phys}} + y_{\text{pace}}\,y_{\text{phys}}$$",
            "",
            r"where $t_0$ is the pick intercept, $h$ the per-pick **handling term** "
            r"($h = c_w w^{e_w} + c_v \log_2 V$; see the pick-time model), $M(y_b)$ the "
            r"height-bracket multiplier, and $D_b$ the entrance-relative **travel** cost "
            r"(low $D$ = front bay). $x_{\text{pace}} = \tfrac{1}{12\,v_x}$ and "
            r"$y_{\text{pace}} = \tfrac{1}{12\,v_y}$ are the per-inch paces for travel speeds "
            r"$v_x$/$v_y$ (ft·s⁻¹).",
            "",
            r"**1. Rank_labor** — travel-aware LPT (longest-processing-time) labor balance. "
            r"Aisle $a$'s total expected labor is $L_a = \sum_{s\in a} f_s\,q_s\,\ell(b_s)$; "
            r"each unit is placed in the aisle–bin pair that least raises the busiest aisle,",
            "",
            r"$$\arg\min_{(a,\,b)}\ \bigl(L_a + f_s\,q_s\,\ell(b)\bigr),$$",
            "",
            r"costliest SKU first. $f_s$ = relative pick frequency (a $[0,1]$ selection share), "
            r"$q_s$ = pick quantity.",
            "",
            r"**2. Map** — optimal-map score matching. Each bin has a quantity-free preferred "
            r"score $\operatorname{pref}(b) = D_b + M(y_b)(t_0 + \bar h)$ ($\bar h$ = mean "
            r"handling term); each SKU's target $\operatorname{target}(s)$ is the "
            r"$\operatorname{pref}$ of its bin in the labor-minimising full linear assignment "
            r"problem (LAP). A unit is placed at",
            "",
            r"$$\arg\min_{b}\ \bigl|\operatorname{pref}(b) - \operatorname{target}(s)\bigr|.$$",
            "",
            r"**3. Map_rank** — the same map, upgrade-capped: a SKU never reloads into a bin "
            r"more prime than its optimal rank,",
            "",
            r"$$\arg\min_{\,b\,:\,\operatorname{pref}(b)\,\ge\,\operatorname{target}(s)}"
            r"\ \bigl(\operatorname{pref}(b) - \operatorname{target}(s)\bigr);$$",
            "",
            r"if no free bin is at or below the SKU's tier, fall back to the least-prime bin.",
        ])

    @env.macro
    def reorder_formula(*args):
        """Equilibrium / reorder-point model with this config's averages."""
        c = _load_cfg(*_ric(args))
        # Single paragraph with INLINE math ($…$) — this macro is rendered inside an
        # indented admonition, where a $$display$$ block (needing its own blank lines)
        # would break out of the call-out. Inline keeps it one logical line.
        return (
            r"$q_{\text{eq}} = \operatorname{round}(\text{coverage}\times \bar d)$ and "
            r"$\text{ROP} = \operatorname{round}(\bar d\,(\text{lead}+\text{safety}))$, "
            r"where $\bar d$ is a SKU's expected per-batch demand. This run's averages: "
            f"equilibrium **{_num(c['avg_equilibrium_qty'])}**, "
            f"reorder point **{_num(c['avg_reorder_point'])}**, "
            f"lead time **{_num(c['avg_lead_time_mean'])}** batches, "
            f"supply CV **{_num(c['avg_supply_cv'])}**."
        )

    @env.macro
    def inv_distribution_table(*args):
        """Per-category distribution table for an inventory variant's params.json."""
        p = _load_params(_inv1(args))
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
    def run_section(*args):
        """Collapsible per-config figure blocks for one run/inventory variant."""
        run, inv = _ri(args)
        out = []
        for cfg, label in _cfg_list():
            out.append(f'??? note "{cfg} — {label}"')
            for fname, caption in _figs("top3"):
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
    def full_suite_section(*args):
        """Collapsible per-config full-suite figure blocks (all strategies) for one
        run/inventory variant — used by the compiled Full-results report."""
        run, inv = _ri(args)
        out = []
        for cfg, label in _cfg_list():
            out.append(f'??? note "{cfg} — {label}"')
            for fname, caption in _figs("full_suite"):
                path = f"images/{run}/{inv}/{cfg}/{fname}"
                out.append(f"    ![{caption}]({path}){{ width=820 }}")
                out.append("")
                out.append(f"    *{caption}*")
                out.append("")
        return "\n".join(out).rstrip()

    @env.macro
    def inv_lead_time(*args):
        """Short human description of a variant's replenishment lead time."""
        p = _load_params(_inv1(args))
        rng = p.get("lead_time_range")
        if not rng:
            return "immediate (0 batches)"
        return f"uniform {_num(rng[0])}–{_num(rng[1])} batches"
