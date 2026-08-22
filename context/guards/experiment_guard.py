"""experiment_guard.py — the current experiment's citations must resolve.

One yes/no question about file text, per the guards charter: does every relative
``data/…`` / ``images/…`` reference in a CURRENT (non-superseded) experiment's pages point at
a file that is actually staged in that experiment's directory — and does every manifest that
declares a what-if block carry a ``schema_id``?

Why it exists: the strict mkdocs build catches a missing file only for paths the macros load;
a hand-typed markdown link to an unstaged file ships silently and 404s on the public site.
The Experiment-8 review loop hit exactly this class (a labor headline citing a rollup CSV the
site had never staged) — this guard makes that a Stop-hook nag instead of a reviewer finding.

Invocation:
    python context/guards/experiment_guard.py --scan     # exit 1 on findings
Wired into ``hook_check.py --stop`` gated on docs/experiments changes (advisory; never blocks).

Second question, same charter (added on the owner's directive after Experiment 8): **no ad-hoc
graphs** — every PNG staged under a current experiment's ``images/`` must be traceable to a
declared producer: a ``figures.yml`` registry name, the run-tree's ``whatif_*.png`` glob, or a
registry inventory plot. A figure a session hand-renders for a documentation step passes the
citation check (the file exists!) but fails THIS one — the fix is always to register the
figure as an evaluation (or whatif artifact) so every future run regenerates it, then re-run
the producer and re-ingest.

Scope limits, deliberate: only experiments whose index.md lacks the "Superseded" banner are
scanned (archived experiments are frozen history — their staging predates the rules); only
relative refs into ``data/`` and ``images/`` are checked (macro-generated paths are the strict
build's job); Jinja-bearing refs (``{{ … }}``) are skipped as macro territory; the provenance
check degrades to a skip if pyyaml is unavailable (the registry is YAML).
"""
from __future__ import annotations

import fnmatch
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, '..', '..'))
_EXP_ROOT = os.path.join(_REPO, 'docs', 'experiments')

# matches (data/x.json) and (images/y.png) style markdown links plus src="data/…" attributes
_REF_RE = re.compile(r'(?:\]\(|src=")((?:data|images)/[^)"#{}\s]+)')


def _current_experiments() -> list[str]:
    out = []
    if not os.path.isdir(_EXP_ROOT):
        return out
    for name in sorted(os.listdir(_EXP_ROOT)):
        d = os.path.join(_EXP_ROOT, name)
        idx = os.path.join(d, 'index.md')
        if not (name.startswith('experiment-') and os.path.isfile(idx)):
            continue
        with open(idx, encoding='utf-8', errors='replace') as fh:
            head = fh.read(2000)
        if 'Superseded' not in head:
            out.append(d)
    return out


def _registry_png_names() -> set[str] | None:
    """Every PNG basename the figure registry declares, or None when pyyaml is absent."""
    try:
        import yaml
    except ImportError:
        return None
    reg_path = os.path.join(_EXP_ROOT, 'figures.yml')
    if not os.path.isfile(reg_path):
        return None
    reg = yaml.safe_load(open(reg_path, encoding='utf-8'))
    return {e['name'] for e in reg.get('figures', []) if isinstance(e, dict) and 'name' in e}


def verify(quiet: bool = False) -> list[str]:
    findings: list[str] = []
    reg_names = _registry_png_names()
    for exp_dir in _current_experiments():
        exp = os.path.basename(exp_dir)
        for fname in sorted(os.listdir(exp_dir)):
            if not fname.endswith('.md'):
                continue
            text = open(os.path.join(exp_dir, fname), encoding='utf-8',
                        errors='replace').read()
            for ref in _REF_RE.findall(text):
                if '{{' in ref or '}}' in ref:
                    continue                      # macro-rendered — the strict build's job
                if not os.path.isfile(os.path.join(exp_dir, *ref.split('/'))):
                    findings.append(f'{exp}/{fname}: cites unstaged file {ref}')
        yml = os.path.join(exp_dir, 'experiment.yml')
        if os.path.isfile(yml):
            mtext = open(yml, encoding='utf-8', errors='replace').read()
            if 'whatif:' in mtext and 'schema_id:' not in mtext:
                findings.append(f'{exp}/experiment.yml: what-if block without schema_id')
        # ad-hoc-graph check: every staged PNG must trace to a declared producer
        if reg_names is not None:
            img_root = os.path.join(exp_dir, 'images')
            for root, _dirs, files in os.walk(img_root):
                for f in files:
                    if (f.endswith('.png') and f not in reg_names
                            and not fnmatch.fnmatch(f, 'whatif_*.png')):
                        rel = os.path.relpath(os.path.join(root, f), exp_dir)
                        findings.append(
                            f'{exp}/{rel.replace(os.sep, "/")}: PNG has no declared producer '
                            f'(not in figures.yml, not whatif_*) — register it as an '
                            f'evaluation so future runs regenerate it')
    if findings and not quiet:
        for f in findings:
            print(f'  [experiment] {f}')
    return findings


def main(argv: list[str]) -> int:
    if argv and argv[0] == '--scan':
        findings = verify()
        if findings:
            print(f'experiment guard: {len(findings)} finding(s)')
            return 1
        print('experiment guard OK - every current-experiment citation resolves')
        return 0
    print(__doc__)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
