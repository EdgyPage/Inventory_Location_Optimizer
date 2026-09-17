"""contractstore — the content-addressed document store that three modules implemented twice.

`Optimization/runschema/contract.py` and `Schema/profile_tree.py` both keep the same thing: a
directory of IMMUTABLE JSON documents named by the short form of their own sha256, plus a mutable
`INDEX.json` carrying `head`, `source_fingerprint` and a provenance chain. Eight operations, and
each module had its own copy of every one.

## The layering objection, and why the conclusion was backwards

Both modules argue in their own docstrings that the duplication is forced: `schema -> optimization`
is a declared boundary ("shape identity must not depend on the run harness"), so `Schema/` cannot
import the run-tree implementation. **The constraint is real and the conclusion is inverted.** The
shared machinery belongs in `Schema/` -- the stdlib-only leaf -- and `runschema/contract.py`
imports DOWN. Satisfied by direction, not by copying.

## What is shared, and what deliberately is not

Shared: the STORE. A content-addressed JSON directory is identical whatever it holds.

Not shared: the DECLARATIONS. `shape_of` -- the projection that defines what a document IS -- is
injected, and so are the sources, the tree directory and the refresh command. An abstraction that
also served two contracts' features, levels and artifacts would be exactly the coupling both
modules were built to avoid, and that half of their objection is correct.

## The copy had already drifted toward weaker invariants

`contract.verify_store()` checked FOUR properties; `profile_tree.verify_store()` checked two --
no filename check, and no parent-chain check even though its own `adopt` writes a `parent` field.
The honesty test meant to keep the two aligned pinned only `source_fingerprint`; nothing pinned
`verify_store`, `adopt`, `write` or `load_all`. One store now means one set of invariants, and the
four apply to both.

`source_fingerprint` was the first operation to come back together (`Schema/fingerprint.py`,
`architecture-drift/05`); this module is the rest of it.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess

from Schema import fingerprint as _fingerprint

__all__ = ['ContractStore']


def _sha(payload: bytes) -> str:
    return 'sha256:' + hashlib.sha256(payload).hexdigest()


class ContractStore:
    """One content-addressed schema store: immutable documents plus a mutable index.

        store = ContractStore(
            tree_dir=..., shape_of=_shape_only, sources=SHAPE_SOURCES,
            refresh_cmd='python -m Schema.profile_tree --write', what='profile-tree')

    Every method that used to be a module-level function in two places is here once. The caller
    keeps its DECLARATION -- what a document contains, and how to build one.
    """

    __slots__ = ('tree_dir', 'index_path', 'shape_of', 'sources', 'source_dirs',
                 'short_len', 'refresh_cmd', 'what', 'diff', 'repo_root')

    def __init__(self, *, tree_dir: str, shape_of, sources, repo_root: str,
                 refresh_cmd: str, what: str, source_dirs=(), short_len: int = 12,
                 diff=None) -> None:
        self.tree_dir = tree_dir
        self.index_path = os.path.join(tree_dir, 'INDEX.json')
        #: `doc -> dict` — the canonical projection the identity is the hash OF.
        self.shape_of = shape_of
        self.sources = tuple(sources)
        #: Auto-discovered directories, for the one store that has such an input.
        self.source_dirs = tuple(source_dirs)
        self.repo_root = repo_root
        self.short_len = short_len
        #: What to tell a human to run. Every staleness message ends in this.
        self.refresh_cmd = refresh_cmd
        #: The noun for messages ('run-tree', 'profile-tree').
        self.what = what
        #: `(old_doc, new_doc) -> list[str]`, or None when this store keeps no change log.
        self.diff = diff

    def __repr__(self) -> str:
        return f'<ContractStore {self.what} at {os.path.basename(self.tree_dir)}>'

    def rebased(self, tree_dir: str) -> 'ContractStore':
        """The same store, pointed at a different directory.

        THE SEAM FOR A THROWAWAY STORE. Redirection used to be done by monkeypatching two module
        globals (`_TREE_DIR`, `_INDEX`), which worked only while every operation re-read them --
        and silently stopped working the moment they were read once at construction. A test that
        patches a global the code no longer consults does not fail; it runs against the REAL
        committed store, which for a test that calls `adopt` is the worst possible outcome.

        So the redirection is an operation rather than a convention, and it carries `shape_of` and
        `diff` with it: a throwaway store that hashed a different projection would prove nothing
        about this one.
        """
        return ContractStore(
            tree_dir=tree_dir, shape_of=self.shape_of, sources=self.sources,
            source_dirs=self.source_dirs, repo_root=self.repo_root, short_len=self.short_len,
            refresh_cmd=self.refresh_cmd, what=self.what, diff=self.diff)

    # ── identity and paths ────────────────────────────────────────────────────────────

    def short_id(self, sid: str) -> str:
        """`'sha256:a412c613ba76…' -> 'a412c613ba76'` — the display and FILENAME form.

        The full id contains a colon, which is an illegal filename character on Windows, so the
        short form names files and the full id lives inside the document.
        """
        return sid.split(':', 1)[-1][:self.short_len]

    def path_for(self, sid: str) -> str:
        """Where the document for an id lives. Accepts a full id or an already-short form."""
        return os.path.join(self.tree_dir, f'{self.short_id(sid)}.json')

    def schema_id(self, doc: dict) -> str:
        """The content-addressed identity: sha256 over the canonical shape, nothing else."""
        return _sha(json.dumps(self.shape_of(doc), sort_keys=True,
                               separators=(',', ':')).encode('utf-8'))

    def source_fingerprint(self, repo_root: str | None = None) -> str:
        """Hash of every shape-defining source, and of any auto-discovered directory.

        `Schema.fingerprint` owns the algorithm; see `architecture-drift/05` for the copy that
        drifted and what it cost.
        """
        h = hashlib.sha256()
        root = repo_root or self.repo_root
        _fingerprint.update_files(h, self.sources, root)
        _fingerprint.update_dirs(h, self.source_dirs, root)
        return 'sha256:' + h.hexdigest()

    # ── the immutable half ────────────────────────────────────────────────────────────

    def load(self, sid: str) -> dict | None:
        """The committed document for an id (full or short), or None when absent."""
        p = self.path_for(sid)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def load_all(self) -> dict[str, dict]:
        """Every committed document, keyed by full id."""
        out: dict[str, dict] = {}
        if not os.path.isdir(self.tree_dir):
            return out
        for fn in sorted(os.listdir(self.tree_dir)):
            if not fn.endswith('.json') or fn == 'INDEX.json':
                continue
            try:
                with open(os.path.join(self.tree_dir, fn), encoding='utf-8') as f:
                    doc = json.load(f)
                out[doc['schema_id']] = doc
            except (json.JSONDecodeError, OSError, KeyError):
                continue
        return out

    def write(self, doc: dict) -> str:
        """Write a document to its versioned path, atomically. Returns the path.

        Two refusals rather than a silent overwrite: a document whose stated id is not its own
        hash is not content-addressed at all, and a SHORT-PREFIX COLLISION would put a different
        schema under this filename -- astronomically unlikely at 48 bits and unrecoverable if it
        ever happened.
        """
        os.makedirs(self.tree_dir, exist_ok=True)
        sid = doc['schema_id']
        assert sid == self.schema_id(doc), (
            'refusing to write a document whose schema_id is not its own hash')
        existing = self.load(sid)
        if existing is not None and existing.get('schema_id') != sid:
            raise RuntimeError(
                f'short-id collision on {self.short_id(sid)}: {existing.get("schema_id")} != '
                f'{sid}. Raise the store\'s short_len and regenerate.')
        path = self.path_for(sid)
        tmp = f'{path}.tmp.{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(doc, f, indent=2, sort_keys=False)
            f.write('\n')
        os.replace(tmp, path)
        return path

    # ── the mutable half ──────────────────────────────────────────────────────────────

    def read_index(self) -> dict:
        """The index, or an empty skeleton when it does not exist yet."""
        try:
            with open(self.index_path, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {'head': None, 'source_fingerprint': None, 'schemas': {}}

    def write_index(self, index: dict) -> str:
        os.makedirs(self.tree_dir, exist_ok=True)
        tmp = f'{self.index_path}.tmp.{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2)
            f.write('\n')
        os.replace(tmp, self.index_path)
        return self.index_path

    def head(self) -> str | None:
        """The id a NEW artifact stamps. A named pointer, because hashes have no natural order."""
        return self.read_index().get('head')

    def adopt(self, doc: dict, *, label: str = '', source_fp: str | None = None,
              commit: str | None = None, created: str | None = None) -> str:
        """Store `doc`, make it the head, record its provenance. Returns its id.

        Idempotent: adopting the current head refreshes the mutable `source_fingerprint` and
        nothing else. The previous head becomes the new entry's `parent`, which is what restores
        the ordering that hashes inherently lack.
        """
        sid = doc['schema_id']
        index = self.read_index()
        prev = index.get('head')
        self.write(doc)
        schemas = index.setdefault('schemas', {})
        if sid not in schemas:
            entry = {
                'short': self.short_id(sid),
                'parent': prev,
                'created': created or _now(),
                'commit': commit if commit is not None else _git_head(self.repo_root),
                'label': label or '',
            }
            if self.diff is not None:
                prev_doc = self.load(prev) if prev else None
                entry['changes'] = (self.diff(prev_doc, doc) if prev_doc
                                    else ['initial schema'])
            schemas[sid] = entry
        elif label and not schemas[sid].get('label'):
            schemas[sid]['label'] = label
        index['head'] = sid
        if source_fp is not None:
            index['source_fingerprint'] = source_fp
        self.write_index(index)
        return sid

    # ── integrity ─────────────────────────────────────────────────────────────────────

    def verify_store(self) -> list[str]:
        """Integrity of the whole committed store. Empty list = sound.

        THE FOUR PROPERTIES that make content addressing trustworthy with no registry:

          1. every stored document re-hashes to its own `schema_id`;
          2. its FILENAME equals `short(schema_id)`;
          3. the index head resolves to a stored document;
          4. every `parent` link resolves, with exactly one root.

        The profiles-tree copy checked only 1 and 3 -- it did not check filenames, and it did not
        check the parent chain although its own `adopt` writes `parent`. That is what a copy
        nothing compares does, and it is why these live here once.
        """
        problems: list[str] = []
        docs = self.load_all()
        if os.path.isdir(self.tree_dir):
            for fn in sorted(os.listdir(self.tree_dir)):
                if not fn.endswith('.json') or fn == 'INDEX.json':
                    continue
                p = os.path.join(self.tree_dir, fn)
                try:
                    with open(p, encoding='utf-8') as f:
                        doc = json.load(f)
                except (json.JSONDecodeError, OSError) as exc:
                    problems.append(f'{fn}: unreadable ({exc})')
                    continue
                recomputed = self.schema_id(doc)
                if doc.get('schema_id') != recomputed:
                    problems.append(
                        f'{fn}: content does not hash to its stated schema_id '
                        f'({doc.get("schema_id")} != {recomputed})')
                if fn != f'{self.short_id(recomputed)}.json':
                    problems.append(
                        f'{fn}: filename != short(schema_id) '
                        f'({self.short_id(recomputed)}.json)')

        index = self.read_index()
        hd = index.get('head')
        if hd and hd not in docs:
            problems.append(f'INDEX head {self.short_id(hd)} has no stored document')
        roots = 0
        for sid, meta in (index.get('schemas') or {}).items():
            if sid not in docs:
                problems.append(f'INDEX lists {self.short_id(sid)} but no document is stored')
            parent = meta.get('parent')
            if parent is None:
                roots += 1
            elif parent not in (index.get('schemas') or {}):
                problems.append(
                    f'{self.short_id(sid)}: parent {self.short_id(parent)} is not in the INDEX')
        if index.get('schemas') and roots != 1:
            problems.append(f'INDEX has {roots} root schema(s); expected exactly 1')
        return problems

    def stale_reasons(self, build) -> list[str]:
        """Cheap staleness findings for the Stop hook -- file hashes and stats only.

        `build` is the caller's declaration-to-document function, taken as an argument rather than
        held, so this module never needs to know what a document contains.
        """
        idx = self.read_index()
        if idx.get('head') is None:
            return [f'the {self.what} schema store is empty - mint it: {self.refresh_cmd}']
        out = []
        fresh = build()
        if fresh['schema_id'] != idx['head']:
            out.append(f'the declaration now hashes to {self.short_id(fresh["schema_id"])} but '
                       f'the head is {self.short_id(idx["head"])} - adopt it: {self.refresh_cmd}')
        if idx.get('source_fingerprint') != self.source_fingerprint():
            out.append(f'a {self.what} shape source changed since the store was last synced - '
                       f'refresh: {self.refresh_cmd}')
        out.extend(f'{self.what} store integrity: {p}' for p in self.verify_store())
        return out


def _now() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _git_head(repo_root: str) -> str:
    """Best-effort provenance: the short commit, or '' when git is unavailable."""
    try:
        r = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=repo_root,
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ''
    except Exception:                       # noqa: BLE001 - provenance never sinks an adopt
        return ''
