"""envfile.py — the ONE `.env` reader, and the one path-value cleaner.

`load_env` and `clean_path` existed four times, byte-for-byte equivalent, in
`Optimization/config/sim_config.py`, `Warehouse/generation/generate_mixed_profile.py`,
`Warehouse/generation/generate_profile_suite.py` and `docs/experiments/ingest.py`. Every copy
had the same skip rules, the same `r"..."` stripping and the same shell-wins precedence, so
they were one function stored four times — the state `Warehouse/operations/putaway.py` records
the cost of: "this project has already paid for two default sets that drifted 55x apart."

WHY IT LIVES HERE, in `Optimization/config/`, and not somewhere more neutral:

* `Warehouse/kernel/` is declared "zero-dependency VALUE OBJECTS — the primitives everything
  else in the DOMAIN is built from". An `.env` reader is harness plumbing and would be the
  first non-domain thing in it.
* A module at the repo root would be STRUCTURALLY INVISIBLE. `context/arch/extract.py`'s
  `GRAPH_ROOTS` are all directories, so a bare top-level `.py` is never walked: no `files.yml`
  entry, no layer, and nothing to notice — the same class of hole this package has been
  fixing elsewhere.
* Reading `.env` IS configuration, and `generation -> opt_config` and `docs -> opt_config` are
  both permitted by `context/architecture.yml` (only the reverse directions are forbidden).

THE IMPORT MUST STAY FREE. `docs/experiments/ingest.py` loads `.env` BEFORE importing anything
that reads it, and `Tests/architecture/test_ingest_env_bootstrap.py` pins that ordering. So
this module imports `os` and nothing else, and both package `__init__` files above it are
docstring-only (`Optimization/__init__.py` says so in as many words: "Deliberately
side-effect-free: no imports here"). Adding an import here can break a bootstrap two packages
away without failing anything local — do not.
"""
from __future__ import annotations

import os

__all__ = ('load_env', 'clean_path')


def load_env(path: str) -> None:
    """Inject `KEY=VALUE` pairs from *path* into `os.environ`.

    Shell-set variables are never overwritten: a value already in the environment wins, which
    is what lets a one-off `COMPARISON_OUTPUT_DIR=... python -m ...` override the file without
    editing it. A missing file is not an error — every caller treats `.env` as optional.

    Blank lines, `#` comments and lines with no `=` are skipped. Values go through
    `clean_path`, so `r"D:\\runs"` and `"D:\\runs"` and `D:\\runs` all arrive the same.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = clean_path(val.strip())


def clean_path(val: str) -> str:
    r"""Strip `r"..."` / `r'...'` notation or plain quotes from an env-var path value.

    Applied after `os.getenv` as well as during parsing, so a value set directly in a Windows
    session environment (with literal `r"..."` text, which the shell does not interpret) is
    normalised exactly like one read from the file.
    """
    if val.startswith(('r"', "r'")):
        return val[2:].rstrip('"').rstrip("'")
    return val.strip('"').strip("'")
