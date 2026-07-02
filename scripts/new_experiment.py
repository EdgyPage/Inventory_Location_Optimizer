"""Scaffold a new docs experiment from the template.

Copies docs/experiments/_template/ -> docs/experiments/<name>/, turning
experiment.yml.example into a starter experiment.yml. With --source it then runs the
ingest to auto-fill experiment.yml (from the run's run_manifest.json) and pull the files.

    python scripts/new_experiment.py --name experiment-2
    python scripts/new_experiment.py --name experiment-2 --source "D:/.../comparison_20260801_120000"

Then: edit experiment.yml (title, short keys, winners), add the nav block it prints to
mkdocs.yml, and `mkdocs serve`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EXPERIMENTS = os.path.join(_REPO, "docs", "experiments")
_TEMPLATE = os.path.join(_EXPERIMENTS, "_template")


def _nav_block(name, title):
    pages = [
        ("Overview", "index.md"),
        ("Simulation lifecycle", "comparison-overview.md"),
        ("Formula reference", "formula-reference.md"),
        ("Comparison", "comparison.md"),
        ("Full results", "full-results.md"),
        ("Inventory distributions", "inventory.md"),
        ("Glossary", "glossary.md"),
    ]
    lines = [f"  - {title}:"]
    for label, fn in pages:
        lines.append(f"      - {label}: experiments/{name}/{fn}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="experiment folder name, e.g. experiment-2")
    ap.add_argument("--source", help="completed run dir; if given, also run ingest --gen-manifest")
    ap.add_argument("--title", default=None, help="nav title (default: the name)")
    args = ap.parse_args(argv)

    dest = os.path.join(_EXPERIMENTS, args.name)
    if os.path.exists(dest):
        sys.exit(f"refusing to overwrite existing {os.path.relpath(dest, _REPO)}")
    if not os.path.isdir(_TEMPLATE):
        sys.exit(f"template not found: {_TEMPLATE}")

    shutil.copytree(_TEMPLATE, dest)
    # example -> real manifest (ingest --gen-manifest may overwrite it from the run)
    example = os.path.join(dest, "experiment.yml.example")
    manifest = os.path.join(dest, "experiment.yml")
    if os.path.isfile(example):
        os.replace(example, manifest)
    print(f"created {os.path.relpath(dest, _REPO)} from _template")

    if args.source:
        # Chain the ingest so the manifest + files are populated in one go.
        sys.path.insert(0, _EXPERIMENTS)
        import ingest  # noqa: E402
        print("\n--- running ingest --gen-manifest ---")
        ingest.main(["--exp", args.name, "--source", args.source, "--gen-manifest"])

    title = args.title or args.name
    print("\nAdd this under `nav:` -> `Experiment N:` in mkdocs.yml:\n")
    print(_nav_block(args.name, title))
    print("\nNext: edit experiment.yml (title, short keys, winners), then `mkdocs serve`.")
    if not args.source:
        print("Populate files with:  python docs/experiments/ingest.py "
              f"--exp {args.name} --source <run_dir> --gen-manifest")


if __name__ == "__main__":
    main()
