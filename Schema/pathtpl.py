"""pathtpl.py — the contract path-template vocabulary: render, and invert-with-capture.

MOVED here from ``Optimization/runschema/resolver.py`` (which re-exports): the profiles-tree
resolver lives in ``Schema/`` — the only layer both the generation writers and the run-harness
consumers may import — and must interpret the SAME template vocabulary the run tree uses.  Two
copies of a template engine is how the two contracts drift apart; one copy in the stdlib-only
leaf is how they cannot.

The vocabulary (shared by both contracts' FEATURES declarations):
  `{name}`   required segment/part
  `{name?}`  OPTIONAL segment — dropped with its separator when the part is None
  `*` / `**` globs; `**` as a whole segment means ZERO or more directories (glob semantics —
             compiling it as one-or-more once silently disowned zero-depth files)
"""
from __future__ import annotations

import re


def render(template: str, **parts) -> str:
    """Render a contract path template to a relative path.

    `{name}` substitutes parts[name]; `{name?}` is an OPTIONAL segment that is dropped along with
    its separator when parts.get(name) is None.  Raises KeyError for a missing required part, so a
    typo fails loudly instead of producing a path with a literal brace in it.
    """
    out = []
    for seg in template.split('/'):
        if seg.startswith('{') and seg.endswith('?}'):
            val = parts.get(seg[1:-2])
            if val is None:
                continue                      # optional segment absent (e.g. store-only channel)
            out.append(str(val))
            continue
        out.append(seg.format(**parts) if '{' in seg else seg)
    return '/'.join(out)


def _capture_regex(template: str, placeholder: str) -> re.Pattern:
    """Compile a template into a matcher that CAPTURES one placeholder from a concrete path.

    Every other `{part}` becomes a wildcard; `{part?}` becomes an optional segment.  This is how
    a resolver inverts `…/sim_{strategy}.db` back to the arm name without hardcoding `len('sim_')`.
    Pass a placeholder that appears in no template (any non-identifier string) to get a pure
    matcher with nothing captured.
    """
    segs = []
    for seg in template.split('/'):
        opt = seg.startswith('{') and seg.endswith('?}')
        body = seg[1:-2] if opt else seg
        if opt:
            segs.append('(?:[^/]+/)?')
            continue
        if body == '**':
            # ZERO-or-more segments, matching glob's semantics for a bare `**` level: a file at
            # the template's zero-depth position (e.g. `compare/top_vs_baseline.png` under
            # `.../compare/**/*.png`) is owned by the template too.  Compiling this as `.*/'
            # required at least one directory and silently disowned exactly those files.
            segs.append('(?:[^/]+/)*')
            continue
        pat = ''
        for tok in re.split(r'(\{\w+\})', body):
            if tok == '{%s}' % placeholder:
                pat += r'(?P<capture>.+)'
            elif tok.startswith('{') and tok.endswith('}'):
                pat += r'[^/]+'
            else:
                pat += re.escape(tok).replace(r'\*\*', '.*').replace(r'\*', '[^/]*')
        segs.append(pat + '/')
    return re.compile('^' + ''.join(segs)[:-1] + '$')
