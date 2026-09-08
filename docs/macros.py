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

Formula *shapes* are fixed here from the code that defines them — the pick-time calibrations
from ``Optimization/run_simulation.py``, the stock levels from
``Optimization/simconfig/coverage.py``, which is where a RUN declares them (the generator
authors none since ADR-0002). Only the *numbers* come from JSON, so pages never hard-code a
value twice.

The one long-standing exception is closed. ``assignment_formulas`` used to transcribe the
top-3 assignment-function *equations* by hand, because no snapshot emitted them; this
docstring asked for "a small registry" so the macro could read them like the others, and
``Optimization/config/objectives.py`` is it. Every run now emits its rule catalogue, and
``rule_catalog()`` renders it — with a test tying each objective to the symbol its builder
actually calls, which is the part hand-maintenance could never offer.

``assignment_formulas`` survives FROZEN, for the pre-dossier experiments whose pages call
it and whose runs have no catalogue staged. Nothing new may use it: a new experiment reads
the artifact.
"""

import json
import os

import yaml

_CACHE = {}


def _verify_manifest_schema(manifest, docs_dir, exp):
    """Check the experiment's recorded run-tree contract still matches the macros' path scheme.

    ``ingest.py`` stamps the source run's ``schema_id`` into ``experiment.yml`` — the content
    address of the run-tree contract the staged snapshot was pulled from
    (``Optimization/schemas/run_tree/<short>.json``).  Every image path a macro constructs is
    ``images/<run>/<inv>/<cfg>/…``, i.e. the contract's three REQUIRED levels
    (cell/pair/config) with the optional ``<channel>`` flattened away by ingest.  If a future
    contract changes those levels, snapshots staged from it no longer mean what these paths say —
    so fail the build LOUDLY here, naming the schema, instead of rendering pages whose figures
    silently come from somewhere else.  Manifests without a ``schema_id`` (Experiments 1–5
    predate the stamp) are exempt: nothing was recorded, so there is nothing to verify.
    """
    sid = manifest.get("schema_id")
    if not sid:
        return                                   # pre-stamp manifest — no contract recorded
    short = str(sid).split(":", 1)[-1][:12]
    repo_root = os.path.dirname(os.path.abspath(docs_dir))
    doc_path = os.path.join(repo_root, "Optimization", "schemas", "run_tree", f"{short}.json")
    if not os.path.isfile(doc_path):
        raise FileNotFoundError(
            f"macros: {exp}/experiment.yml records run-tree schema {sid}, but no contract "
            f"document is committed at Optimization/schemas/run_tree/{short}.json. "
            f"Check out the commit that produced the run, or re-ingest the experiment."
        )
    with open(doc_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    levels = doc.get("levels", [])
    required = [lv["name"] for lv in levels if not lv.get("optional")]
    optional = [lv["name"] for lv in levels if lv.get("optional")]
    if required != ["cell", "pair", "config"] or optional != ["channel"]:
        raise ValueError(
            f"macros: run-tree schema {sid} ({exp}) declares levels "
            f"required={required} optional={optional}, but the macros build image paths as "
            f"images/<run(cell)>/<inv(pair)>/<cfg(config)>/... with <channel> flattened. "
            f"The staged snapshot no longer matches this convention — update docs/macros.py "
            f"and docs/experiments/ingest.py together before rebuilding."
        )


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

    _schema_checked = set()

    def _manifest():
        """The parsed experiment.yml for the current page's experiment, or None (legacy).

        In manifest mode the manifest's recorded `schema_id` (if any) is verified against the
        committed run-tree contract ONCE per experiment per build — a mismatch raises, which
        `mkdocs build --strict` turns into a build failure."""
        exp = _exp_dir()
        rel = f"{exp}/experiment.yml"
        abs_path = os.path.join(docs_dir, *rel.split("/"))
        if not os.path.isfile(abs_path):
            return None
        m = _load_yaml(rel)
        if m and exp not in _schema_checked:
            _verify_manifest_schema(m, docs_dir, exp)
            _schema_checked.add(exp)
        return m

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

    def _registry():
        """The figure registry (experiments/figures.yml) — THE caption/section/default
        source.  This module once carried three hardcoded caption tables that drifted
        independently of ingest's staging lists and every experiment.yml; now all three
        consumers read one declaration, and Tests/architecture/test_figure_registry.py
        ties it to the code that writes each figure."""
        return _load_yaml("experiments/figures.yml").get("figures", [])

    def _figs(kind):
        """[(filename, caption, width)] for kind in {'top3','full_suite','run_suite'}.

        A manifest names its own figures.  WITHOUT one the experiment is a legacy
        snapshot, and it renders the registry's `legacy` set — frozen to what that sweep
        actually produced — NOT the `default` starter set, which names the figures the
        CURRENT suite writes and which a 2026-06 run never had.  Conflating the two
        pointed Experiment 1 at chart names that postdate it by two months."""
        m = _manifest()
        entries = _registry()
        caps = {f["name"]: (f.get("caption") or f["name"], f.get("width", 820))
                for f in entries}
        if m:
            return [(n, *caps.get(n, (n, 820)))
                    for n in m.get("figures", {}).get(kind, [])]
        return [(f["name"], *caps[f["name"]]) for f in entries
                if f["section"] == kind and f.get("legacy")]

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

    @env.macro
    def run_commit():
        """Which simulator CODE produced this experiment's run, rendered for a caption.

        The one ``schema_id`` a page can already cite names the run's DB **table** contract, and a
        change that moves no table is byte-identical in it: ``753d01e`` shifted absolute throughput
        ~1.4 % and left every id on the run unchanged. This reads the commit that
        ``Optimization/runschema/sim_manifest.py`` stamps into ``run_spec.json`` and
        ``docs/experiments/ingest.py`` carries into ``experiment.yml``.

        Two lookup sites, because a run has two names. A **what-if** experiment's ``run:`` is a
        CELL inside the sweep, so the run root — and therefore its commit — lives beside
        ``whatif.source_run``; an ordinary experiment's ``run:`` IS the run and its commit sits at
        the top level next to ``schema_id``, which is where ingest writes it.

        Runs that predate the stamp render as *not recorded* rather than silently as nothing: the
        absence is the point, and a page that quietly omitted it would be the same invisible gap
        this macro exists to close.
        """
        m = _manifest() or {}
        c = (m.get("whatif") or {}).get("commit") or m.get("commit")
        dirty = (m.get("whatif") or {}).get("dirty")
        if dirty is None:
            dirty = m.get("dirty")
        if not c or c == "unknown":
            return "code commit not recorded"
        # `+ uncommitted changes` is not decoration: a dirty tree means the commit alone does not
        # identify what ran, so a reader must not treat the sha as reproducible.
        return f"code <code>{c}</code>" + (" <strong>+ uncommitted changes</strong>" if dirty else "")

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
        """Human-legible M(y) brackets for a calibration, e.g. '×1.0 (y<96″) · …'.
        Configs with no height brackets (e.g. the fulfillment channel, whose short bins
        never reach a higher tier) render as flat ×1."""
        hb = _hbrackets(cfg)
        if not hb:
            return "— (flat ×1, no height scaling)"
        (e1, m1), (e2, m2), (_, m3) = hb
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
            # These two keys survived ADR-0002; their MEANING moved.  A catalogue authors no
            # stock level any more, so the averages are over the levels THIS RUN fielded —
            # declared at setup from a coverage in days — not a property the inventory came
            # with.  The label says whose number it is, and it stays true of a pre-ADR archive
            # too: that run fielded its levels as well, it just inherited rather than declared
            # them.  `reorder_formula` renders which of the two a given snapshot describes.
            ("Equilibrium qty (avg, this run's levels)", _num(c["avg_equilibrium_qty"])),
            ("Reorder point (avg, this run's levels)", _num(c["avg_reorder_point"])),
            ("Lead time (avg batches)", _num(c["avg_lead_time_mean"])),
            ("Supply CV (avg)", _num(c["avg_supply_cv"])),
        ]
        lines = ["| Parameter | Value |", "|-----------|-------|"]
        lines += [f"| {k} | {v} |" for k, v in rows]
        return "\n".join(lines)

    @env.macro
    def whatif_matrix():
        """Cross-cell delta table for a what-if experiment, from data/whatif_delta.json.

        One row per matrix cell, per-channel **median** Δ(task makespan = labor) and
        Δ(throughput / batch makespan) vs the reference cell (generated from whatif_delta.csv by the
        run's post-processing). Only a WHAT-IF experiment has this JSON; ordinary single-run
        experiments never call this macro. Numbers live in the committed JSON, so the page holds only
        prose.  Key-tolerant: prefers the four-metric keys (dthr_batch/dtask_ms/dbatch_ms/dthr_task)
        and falls back to the legacy dthr/dlabor so older experiment JSON still renders."""
        d = _load_json(f"{_exp_dir()}/data/whatif_delta.json")

        def pct(v):
            return f"{v:+.1f}%"

        def _med(ch, *keys):   # first present key's median, tolerant of the legacy schema
            for k in keys:
                if ch and k in ch and ch[k] is not None:
                    return ch[k].get("med")
            return None

        def thr(ch):   # throughput / batch makespan — the headline; bold it
            v = _med(ch, "dthr_batch", "dthr")
            return f"**{pct(v)}**" if v is not None else "—"

        def lab(ch):   # task makespan (= total labor)
            v = _med(ch, "dtask_ms", "dlabor")
            return pct(v) if v is not None else "—"

        lines = [
            "| Cell | Layout | Zoning | Store Δthr/batch | Store Δtask-ms | Fulf Δthr/batch | Fulf Δtask-ms |",
            "|------|--------|--------|----------------:|--------------:|---------------:|-------------:|",
        ]
        for c in d["cells"]:
            s = c["by_channel"].get("store")
            f = c["by_channel"].get("fulfillment")
            lines.append(
                f"| `{c['name']}` | {c['layout']} | {c['zoning_label']} "
                f"| {thr(s)} | {lab(s)} | {thr(f)} | {lab(f)} |"
            )
        ref = d.get("reference", "baseline")
        cap = (
            f"\n<small>Steady-state medians vs the `{ref}` baseline, last-50-batch window; "
            f"{d.get('arms')} arms × pick-configs × {len(d.get('pairs', []))} inventories per cell. "
            f"Δthr/batch = throughput ÷ batch makespan; Δtask-ms = task makespan (Σ task time = labor). "
            f"Batch-makespan and throughput ÷ task-makespan deltas are in whatif_delta.csv. "
            f"**+ = better** (more throughput / less labor).</small>"
        )
        return "\n".join(lines) + "\n" + cap

    @env.macro
    def pick_time_formula(*args):
        """Pick-time cost model (LaTeX) with this config's calibrated coefficients.
        Matches Warehouse/picking/Pick.py:
            t_pick = M(y)·(t0 + q·t1 + q·h) + c_cart·1[cart swap]
        where t1 is the per-item charge (ADR-0001).  An archived config WITHOUT a
        `pick_per_item` key predates the charge: it ran the model without the term, so
        the term is rendered absent rather than at this checkout's default — the page
        describes the run, not the current code."""
        c = _load_cfg(*_ric(args))
        t0 = _num(c["pick_intercept"])
        t1 = c.get("pick_per_item")           # None on a pre-charge archive
        wt = _fmt_fn_tex(c["pick_weight_coef"], c["pick_weight_fn"], "w")
        vt = _fmt_fn_tex(c["pick_volume_coef"], c["pick_volume_fn"], "V")
        cart = _num(c["cart_swap_coef"])
        vx, vy = c["x_speed"], c["y_speed"]
        dx, dy = _num(12 * vx), _num(12 * vy)
        item_term = "" if t1 is None else r" + q\," + _num(t1)
        item_gloss = ("" if t1 is None else
                      r" $t_1$ the per-item charge (s per unit picked),")
        return "\n".join([
            r"$$t_{\text{pick}} \;=\; M(y)\,\bigl(" + t0 + item_term + r" + q\,h\bigr)"
            r" \;+\; " + cart + r"\,\mathbb{1}[\text{cart swap}],"
            r"\qquad h \;=\; " + wt + " + " + vt + r"$$",
            "",
            r"$$D \;=\; \frac{x_{\text{phys}}}{" + dx + r"} + \frac{y_{\text{phys}}}{" + dy +
            r"}\ \text{s}\qquad(\text{speeds } " + _num(vx) + "/" + _num(vy) +
            r"\ \text{ft·s}^{-1}\text{, cross-aisle / along-aisle}).$$",
            "",
            r"Here $t_0$ is the fixed setup per pick line (s, one bin visit for one SKU),"
            + item_gloss +
            r" $q$ the quantity picked, $w$ the item "
            r"weight (lb), $V$ its volume (in³), $y$ the shelf height, $M(y)$ the height-bracket "
            r"multiplier (per calibration below), $h$ the per-unit **handling term**, "
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
        """FROZEN. Hand-transcribed equations for the top-3 assignment functions.

        Superseded by `rule_catalog()`, which renders the catalogue the run itself emits
        from `Optimization/config/objectives.py` — all 17 rules, each tied by test to the
        symbol its builder actually calls. This survives only for the experiments whose
        runs predate the dossier and whose pages still call it; those pages must keep
        building, and their runs have no catalogue to read.

        Nothing new may use it. A new experiment calls `rule_catalog()`.
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

    # ── run-scope documents: claims rendered FROM the artifact ────────────────
    # Every macro below composes a sentence or a table out of a document the ANALYSIS
    # emitted.  The page then carries the CALL, not the number — which is the whole point:
    # a count, a formula or a rule objective typed into prose can drift from the run, and
    # every one of these replaced a claim that had.
    #
    # One loader, one name dict.  The filename ratchet counts raw text, so spelling each
    # document name exactly once keeps this file's contribution to it at one apiece — and
    # nothing here may be NAMED after the reserved directory either, because the ratchet
    # derives a token from that name too and an identifier containing it counts.

    _RUN_DOCS = {
        "census":    "comparison_census.json",
        "rules":     "rule_catalog.json",
        "inventory": "inventory_model.json",
        "fixed":     "held_fixed.json",
        "cost":      "dossier.json",
    }

    def _run_doc(kind):
        """A staged dossier document by short name; raises when it is not staged.

        Loud on purpose, matching `_verify_manifest_schema`: under `--strict` a missing
        document must fail the build.  A macro that degraded to a blank sentence would put
        an EMPTY claim on a published page, which is worse than no page at all.
        """
        return _load_json(f"{_exp_dir()}/data/{_RUN_DOCS[kind]}")

    def _stock_declaration(inv):
        """This experiment's stock-level DECLARATION for one pair, or None on a pre-ADR-0002
        snapshot.  ``{}`` means "declared, but this snapshot does not record the inputs".

        The soft counterpart to `_run_doc`, and the exception is earned: here the absence is
        DATA, not a staging mistake.  Two absences, and they mean the same thing —

          * no inventory document staged at all (Experiments 1–7 predate the run-scope
            documents entirely), and
          * a document whose pairs still carry `rop_formulas`, the closed-form scores that
            only ever existed while a CATALOGUE authored the levels (Experiment 8).

        Both identify a run whose levels came with its inventory rather than from its own
        declaration, and its page must keep describing what it ran — the same rule
        `pick_time_formula` follows for a config that predates the per-item charge.  The
        vintage is read across the whole document, never per pair, so a pair this page cannot
        name never flips an archive into the current model by accident.
        """
        rel = f"{_exp_dir()}/data/{_RUN_DOCS['inventory']}"
        if not os.path.isfile(os.path.join(docs_dir, *rel.split("/"))):
            return None
        pairs = _load_json(rel).get("pairs") or {}
        if any("rop_formulas" in p for p in pairs.values()):
            return None
        name = next((k for k in sorted(pairs) if inv and inv in k), None)
        return (pairs.get(name) or {}).get("declaration") or {}

    def _floor_lines(v):
        """`floor_lines` in words: "1 line", "1.5 lines".

        The floor is declared in LINES of the SKU's own mean line, so the singular earns its
        keep — the default declares exactly one pick's worth of the SKU, and "1 line(s)" on a
        published page reads like a macro that could not be bothered."""
        return f"{_num(v)} line" + ("" if abs(float(v) - 1.0) < 1e-9 else "s")

    def _unused_stock_declaration_tail(inv, pairs):
        if any("rop_formulas" in p for p in pairs.values()):
            return None
        name = next((k for k in sorted(pairs) if inv and inv in k), None)
        return (pairs.get(name) or {}).get("declaration") or {}

    def _census_row(metric, grouping, group=None):
        doc = _run_doc("census")
        for b in doc.get("blocks", []):
            if b["metric"] != metric or b["grouping"] != grouping:
                continue
            for r in b["rows"]:
                got = next((v for k, v in r.items() if k.startswith("group_")), None)
                if (group is None and got is None) or (group is not None and got == group):
                    return r
        raise KeyError(f"macros: no census row for metric={metric!r} "
                       f"grouping={grouping!r} group={group!r}")

    def _pct(v, places=1):
        return "—" if v is None else f"{v:+.{places}f} %"

    @env.macro
    def census_claim(metric, grouping="channel", group=None, places=1):
        """One count/span claim, composed from the census artifact.

        Renders the sentence the pages used to type by hand beside a figure: how many
        comparisons went the claimed way, the exact sign test and its interval, the median
        and the span.
        """
        r = _census_row(metric, grouping, group)
        n, pos = r["n"], r["n_pos"]
        head = (f"positive in **all {n}** comparisons" if pos == n and n
                else f"positive in **{pos} of {n}** comparisons")
        p = r.get("p_sign")
        test = ("" if p is None else
                f" (sign test *p* = {p:.1e}, 95 % CI "
                f"[{r['ci_lo']:.3f}, {r['ci_hi']:.3f}])")
        return (f"{head}{test}, median **{_pct(r['median'], places)}**, "
                f"range {_pct(r['min'], places)} to {_pct(r['max'], places)}")

    @env.macro
    def census_table(metric, grouping="channel", places=1):
        """The full census grid for one metric — every group, plus the overall row."""
        doc = _run_doc("census")
        block = next((b for b in doc.get("blocks", [])
                      if b["metric"] == metric and b["grouping"] == grouping), None)
        if block is None:
            raise KeyError(f"macros: no census block {metric!r}/{grouping!r}")
        gcols = [k for k in block["rows"][0] if k.startswith("group_")]
        head = " | ".join(c[len("group_"):] for c in gcols)
        out = [f"| {head} | comparisons | went the claimed way | median | range | sign test |",
               "|" + "---|" * (len(gcols) + 5)]
        for r in block["rows"]:
            label = " | ".join(str(r[c] if r[c] is not None else "**all**") for c in gcols)
            p, floor = r.get("p_sign"), r.get("p_floor")
            # A p is meaningless without the smallest value its n could reach.  At n=4 the
            # floor is 0.0625, so a rule that went the claimed way EVERY time still reads
            # as "not significant" against a habitual 0.05 — the test ran out of room, not
            # the effect.  Saying so is the difference between a number and a conclusion.
            ptxt = "—" if p is None else (
                f"{p:.1e}" if floor is None or p > floor * 1.001
                else f"{p:.1e} (the floor for n={r['n_pos'] + r['n_neg']})")
            out.append(
                f"| {label} | {r['n']} | {r['n_pos']} | {_pct(r['median'], places)} | "
                f"{_pct(r['min'], places)} to {_pct(r['max'], places)} | {ptxt} |")
        out += ["",
                f"<small>{block['label']}. Source: the run's comparison census "
                f"({doc['n_comparisons']} same-rule comparisons against cell "
                f"<code>{doc['reference_cell']}</code>).</small>"]
        return "\n".join(out)

    @env.macro
    def rule_catalog(only=None, family=None):
        """The placement-rule catalogue, generated from the registry the run emitted.

        Replaces per-rule prose that existed in three places at once.  Anchors reproduce
        the ones the docs already link to (`#rank-labor`, `#map`, …) — `docref_guard`
        checks a different reference form, so a renamed anchor here would be a silently
        broken published link.
        """
        doc = _run_doc("rules")
        rules = [r for r in doc["rules"]
                 if (only is None or r["rule"] in only)
                 and (family is None or r["family"] == family)]
        out, seen = [], set()
        for r in rules:
            if only is None and r["family"] not in seen:
                seen.add(r["family"])
                out += [f"## {r['family_label']}", ""]
            out.append(f"### {r['label']} — `{r['rule']}` {{ #{r['anchor']} }}")
            if r["control"]:
                out.append("*A deliberate worst-case control — designed to lose. The "
                           "distance to its mirror is what the lever is worth.*")
            out.append(r["notes"])
            if r["latex"]:
                out += ["", r["latex"], ""]
            bits = [f"Implemented by `{r['symbol']}` in `{r['module']}`"]
            if r["precompute"]:
                bits.append("with an **offline build step** "
                            f"(`{r['precompute'].split('@')[0]}`) run once per arm before "
                            "the simulation")
            # The exact/greedy split is NOT appended here: `catalog.rules` folds the
            # measurement into the emitted note itself, so the data file and this page say
            # the same thing without a reader having to know a macro exists.
            if r.get("map_lap_scope"):
                bits.append(f"measured {r['map_lap_scope']}")
            if not r["ran"]:
                bits.append("**not swept in this run**")
            out += [f"<small>{'; '.join(bits)}.</small>", ""]
        return "\n".join(out)

    def _stock_footnote(m):
        """The sentence under the distribution table — one per document vintage.

        A snapshot staged BEFORE ADR-0002 carries `rop_formulas`: the closed forms the
        catalogue evaluation scored against the levels its CATALOGUE authored, published
        because "neither form holds" deserved to be a measurement rather than an
        assertion.  Those experiments keep the sentence they published, word for word —
        it describes what they ran, exactly as `pick_time_formula` keeps rendering a
        config that predates the per-item charge without the term.

        A snapshot WITHOUT the block is post-ADR-0002, and there the score has no subject
        left: no closed form authors a level, so reproducing one would measure the
        agreement of two things the run never used.  What this says instead is whose
        numbers these are and which declaration produced them.
        """
        forms = m.get("rop_formulas")
        if forms:
            best = max(forms, key=lambda f: f["match_pct"])
            return (f"<small>Measured over the run's own {m['n_skus']:,} stocked SKUs. The "
                    f"closest closed form for the reorder point reproduces only "
                    f"**{best['match_pct']:.0f} %** of the stored values, because the "
                    f"planner's figures are rescaled to fit the warehouse after they are "
                    f"computed — the distribution above is what the simulation actually "
                    f"stocked.</small>")
        n_dec = m.get("n_declared")
        scope = (f"Measured over the {n_dec:,} of {m['n_skus']:,} SKUs this run declared "
                 f"a level for" if n_dec is not None
                 else f"Measured over the run's own {m['n_skus']:,} stocked SKUs")
        # The three knobs, or an explicit silence.  An empty declaration is a run whose
        # assets were rebuilt from a frozen inventory: it fielded levels, it just did not
        # record what set them — a different statement from "10 days", and it must read
        # as one rather than fall back on this checkout's defaults.
        d = m.get("declaration") or {}
        decl = (f", at a declared **{_num(d['coverage_days'])} days** of coverage "
                f"(+ {_num(d['safety_days'])} days of safety, floored at "
                f"{_num(d['floor_lines'])} line(s) of the SKU's own demand)"
                if len(d) == 3 else ", at a coverage this snapshot does not record")
        plans = m.get("n_stock_plans") or 0
        packed = (f" {plans:,} of them carry a hand-written packing plan, which overrides "
                  f"the pallet/singleton rule." if plans else "")
        return (f"<small>{scope}{decl}. The levels are the RUN's declaration, derived at "
                f"setup from each SKU's expected demand per DAY — a catalogue authors "
                f"none (ADR-0002), so there is no generator formula left for them to "
                f"reproduce and none is scored here. The distribution above is what the "
                f"simulation actually stocked.{packed}</small>")

    @env.macro
    def inventory_model(inv_key=None):
        """The realised inventory model — distributions, not a formula.

        Publishing a distribution is not a retreat from a formula that would not hold: a
        stock level is a RUN's declaration, never a SKU's fact (ADR-0002).  The catalogue
        carries none, every run derives its own at setup from a coverage in DAYS, and
        what that produced across the SKUs it fielded IS the model — there is nothing
        closed-form behind it to publish instead.

        The table renders whichever quantities the staged document carries, so each
        vintage shows its own: a pre-ADR-0002 snapshot measured the per-wave demand its
        authored level was coverage of, a current one measures the lead pipeline the run
        stamps.  `_stock_footnote` carries the sentence underneath.
        """
        doc = _run_doc("inventory")
        pairs = doc["pairs"]
        key = _inv1((inv_key,)) if inv_key else None
        name = next((k for k in sorted(pairs) if key and key in k), sorted(pairs)[0])
        m = pairs[name]
        d = m["distributions"]
        labels = [("equilibrium_qty", "equilibrium quantity (units held per SKU)"),
                  ("reorder_point", "reorder point (units)"),
                  ("pipeline_qty", "lead pipeline (units allowed in transit)"),
                  ("lead_time_mean", "lead time (waves)"),
                  ("expected_batch_demand", "expected demand per wave (units)")]
        rows = ["| quantity | median | mean | p5 – p95 | min – max |",
                "|---|---:|---:|---:|---:|"]
        for k, label in labels:
            v = d.get(k) or {}
            if not v:
                continue
            rows.append(f"| {label} | {v['median']:,.2f} | {v['mean']:,.2f} | "
                        f"{v['p5']:,.2f} – {v['p95']:,.2f} | "
                        f"{v['min']:,.2f} – {v['max']:,.2f} |")
        rows += ["", _stock_footnote(m)]
        return "\n".join(rows)

    @env.macro
    def held_fixed_table():
        """What the sweep varied, what it held fixed, and what it has no knob for."""
        doc = _run_doc("fixed")
        out = ["| factor | levels in this run |", "|---|---|"]
        for r in doc["varied"]:
            vals = ", ".join(f"`{v}`" for v in r["levels"][:6])
            out.append(f"| **{r['label']}** (varied) | {r['n_levels']} — {vals} |")
        for r in doc["fixed"]:
            out.append(f"| {r['label']} | held at `{r['levels'][0]}` |")
        for r in doc["absent"]:
            out.append(f"| {r['factor'].replace('_', ' ')} | **not a parameter of this "
                       f"model** — {r['why']} |")
        return "\n".join(out)

    @env.macro
    def rule_cost_table(channel="store", top=None):
        """What each rule costs to RUN — real CPU seconds, not modeled warehouse labor."""
        doc = _run_doc("cost")
        rows = [r for r in doc["cost"] if r["channel"] == channel]
        rows.sort(key=lambda r: (r["x_reord_vs_fifo"] is None, r["x_reord_vs_fifo"] or 0.0))
        if top:
            rows = rows[:top]
        out = ["| rule | vs do-nothing | per unit put away | per wave | offline build |",
               "|---|---:|---:|---:|---:|"]
        for r in rows:
            x, ms = r["x_reord_vs_fifo"], r["scoring_ms_per_unit"]
            per_wave, pre = r["reord_s_per_wave"], r["precomp_s"]
            build = ("—" if not r["has_precompute"]
                     else ("unmeasured" if pre is None else f"{pre:,.0f} s"))
            out.append(
                f"| {r['label']}{' *(control)*' if r['control'] else ''} "
                f"| {'—' if x is None else f'{x:.2f}×'} "
                f"| {'—' if ms is None else f'{ms:.3f} ms'} "
                f"| {'—' if per_wave is None else f'{per_wave:.2f} s'} "
                f"| {build} |")
        w = doc.get("workers")
        contention = (f"contended against {w} workers" if w else "contention unrecorded")
        out += ["",
                f"<small>Wall-clock CPU time on the machine that ran the sweep, "
                f"{contention}; the multiple against the do-nothing rule is the figure that "
                f"travels between machines. The offline build is measured separately and is "
                f"NOT part of the per-wave column — it runs once per arm, before the "
                f"simulation starts.</small>"]
        return "\n".join(out)

    @env.macro
    def reorder_formula(*args):
        """How this run's stock levels were SET, and the averages it fielded.

        Two vintages, told apart by `_stock_declaration` off the staged inventory
        document, and the page describes ITS OWN run either way:

          * a run that DECLARED its levels (ADR-0002 onward) gets the derivation it
            actually used — a coverage in DAYS against each SKU's expected daily demand,
            floored at a line of that SKU's own demand — with the declared inputs quoted
            when the snapshot records them;
          * an archived run gets the authored model it ran, NAMED as retired: its
            catalogue carried the levels and the planner rescaled them to fit the
            warehouse afterwards, which is why no closed form reproduced the stored
            values.

        What this will not do is print an equation beside numbers it does not produce.
        The archived branch says so outright; the declared branch's formulas ARE the ones
        that produced its averages, which is the whole of the difference ADR-0002 made.
        `inventory_model()` publishes the distribution behind the averages in both.
        """
        run, inv, cfg = _ric(args)
        c = _load_cfg(run, inv, cfg)
        decl = _stock_declaration(inv)
        # Single paragraph with INLINE math ($…$) — this macro is rendered inside an
        # indented admonition, where a $$display$$ block (needing its own blank lines)
        # would break out of the call-out. Inline keeps it one logical line.
        averages = (f"This run's averages: "
                    f"equilibrium **{_num(c['avg_equilibrium_qty'])}**, "
                    f"reorder point **{_num(c['avg_reorder_point'])}**, "
                    f"lead time **{_num(c['avg_lead_time_mean'])}** batches, "
                    f"supply CV **{_num(c['avg_supply_cv'])}**.")
        if decl is None:
            return (
                r"Each SKU carried an **equilibrium quantity** (its steady-state stock) "
                r"and a **reorder point** (the level that triggers replenishment), and "
                r"this run took both from its catalogue: derived there from the SKU's "
                r"expected per-batch demand $\bar d$ and its lead time, then rescaled so "
                r"the whole catalogue fitted the warehouse — which is why no closed form "
                r"reproduces the stored values. That model is **retired** (ADR-0002): a "
                r"catalogue authors no stock level now, and every run declares its own "
                r"from a coverage in days. " + averages
            )
        # `{}` = declared, inputs unrecorded.  Naming the record beats printing this
        # checkout's defaults into a page about someone else's run.
        inputs = (f"This run declared $C$ = **{_num(decl['coverage_days'])} days**, "
                  f"$S$ = **{_num(decl['safety_days'])} days** and $F$ = "
                  f"**{_num(decl['floor_lines'])} line(s)**. " if len(decl) == 3 else
                  "The three inputs are recorded per pair in the run's own staffing "
                  "record; this snapshot does not carry them. ")
        return (
            r"A stock level is not a property of a SKU — the catalogue carries none "
            r"(ADR-0002) — so this run **declared** both at setup from each SKU's "
            r"expected demand per **day** $d_s$: its share of the lines the crew fills "
            r"in a day, times its own mean line $\bar q_s$. Against a declared coverage "
            r"$C$, safety $S$ and a floor of $F$ lines, the **equilibrium quantity** (its "
            r"steady-state stock) and the **reorder point** (the level that triggers "
            r"replenishment) are $L_s = \lceil F\,\bar q_s \rceil$, "
            r"$Q_s = \max(\mathrm{round}(C\,d_s),\,L_s)$ and "
            r"$r_s = \min(Q_s - 1,\ \max(\mathrm{round}(d_s\,(\ell_s + S)),\,L_s))$ "
            r"for a lead time $\ell_s$ — so a SKU whose coverage is shorter than the gap "
            r"between its own lines sits on the floor at $r_s = Q_s - 1$ and runs base "
            r"stock. " + inputs + averages
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
    @env.macro
    def run_section(*args):
        """Collapsible per-config figure blocks for one run/inventory variant."""
        run, inv = _ri(args)
        out = []
        for cfg, label in _cfg_list():
            out.append(f'??? note "{cfg} — {label}"')
            for fname, caption, width in _figs("top3"):
                path = f"images/{run}/{inv}/{cfg}/{fname}"
                out.append(f"    ![{caption}]({path}){{ width={width} }}")
                out.append("")
                out.append(f"    *{caption}*")
                out.append("")
        return "\n".join(out).rstrip()

    @env.macro
    def full_suite_section(*args):
        """Collapsible per-config full-suite figure blocks (all strategies) for one
        run/inventory variant — used by the compiled Full-results report."""
        run, inv = _ri(args)
        out = []
        for cfg, label in _cfg_list():
            out.append(f'??? note "{cfg} — {label}"')
            for fname, caption, width in _figs("full_suite"):
                path = f"images/{run}/{inv}/{cfg}/{fname}"
                out.append(f"    ![{caption}]({path}){{ width={width} }}")
                out.append("")
                out.append(f"    *{caption}*")
                out.append("")
        return "\n".join(out).rstrip()

    @env.macro
    def run_suite_section():
        """The RUN-scope figure blocks — one set per run, not one per config leaf.

        `full_suite_section` composes `images/{run}/{inv}/{cfg}/{fname}`, which is right for
        every figure a channel-run leaf produces and wrong for one the run root produces.
        The `cost` family renders at run scope and ingest stages it flat, so it gets its own
        section and its own path here.  A registry test ties the section to the
        evaluation's declared scope in both directions, so neither can move alone.
        """
        out = []
        for fname, caption, width in _figs("run_suite"):
            out.append(f'![{caption}](images/{fname}){{ width={width} }}')
            out.append("")
            out.append(f"*{caption}*")
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
