"""render.py — shared data loaders for the architecture renderers.

Loads the verified architecture data (graph.json + architecture.yml + files.yml) and
provides the stable node-id slugger + layer mapping.  The human-facing rendering now lives
in context/arch/render_html.py (the static HTML code-map suite); this module is the small
data seam it imports (`_load`, `_mid`, `_layer_of`).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CTX = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_CTX)

try:
    import yaml
except ImportError:                                        # pragma: no cover
    sys.exit('render needs pyyaml (pip install -r requirements-docs.txt)')

_extract = None


def _layer_of(relpath, layers):
    """Delegate to extract.layer_of (loaded lazily) so layer logic lives in one place."""
    global _extract
    if _extract is None:
        spec = importlib.util.spec_from_file_location('arch_extract', os.path.join(_HERE, 'extract.py'))
        _extract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_extract)
    return _extract.layer_of(relpath, layers)


def _load():
    """Return (graph, arch, files) — the derived graph, the curated spec, the file catalog."""
    graph = json.load(open(os.path.join(_HERE, 'graph.json'), encoding='utf-8'))
    arch = yaml.safe_load(open(os.path.join(_CTX, 'architecture.yml'), encoding='utf-8')) or {}
    files = {}
    fp = os.path.join(_CTX, 'files.yml')
    if os.path.isfile(fp):
        files = (yaml.safe_load(open(fp, encoding='utf-8')) or {}).get('files', {})
    return graph, arch, files


def _mid(s: str) -> str:
    """Stable, filesystem/id-safe slug for a node id (alnum kept, everything else -> '_')."""
    return 'n' + ''.join(c if c.isalnum() else '_' for c in s)
