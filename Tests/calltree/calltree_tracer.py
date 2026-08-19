"""
calltree_tracer.py — deterministic call-tree tracer for the runtime measurement framework.

Why this exists
---------------
The performance question this framework answers is "which call paths cost, and how do their
call COUNTS scale" — a question sampling profilers cannot answer (no counts) and cProfile
answers only approximately (caller/callee pairs, not paths, and it is blind to the worker
threads `fast_pick` spawns per batch). This tracer is stdlib-only (`sys.setprofile` +
`threading.setprofile`), aggregates frames on the fly into a path-keyed tree (no per-event
log), and merges worker-thread trees by call path under the section active when the thread
first ran.

Attribution model
-----------------
Only PROJECT frames (Warehouse/, Optimization/, Schema/) become timed tree nodes. Non-project
Python frames are TRANSPARENT: they never break a path — a project function called from a
test driver, a contextmanager, or a `sorted(key=...)` callback attaches to the nearest
project ancestor (or the active section root). Externals still appear as count-only leaves
(`<ext>:file:fn`, `<c>:module.fn`, cum_s = 0): their wall time deliberately rolls into the
calling project frame's self_s, because the question "is this project function expensive"
must include the C/library work it commissions.

The price of determinism is overhead (tracing multiplies wall time several-fold), which is
why captures are two-pass: an UNTRACED pass owns the wall-clock truth (plain perf_counter
section timers, the same idiom as strategy_runner's t_* accumulators), and the TRACED pass
owns tree shape, call counts, and relative attribution. Traced timings are never regression
baselines.

Vocabulary: section names are the exact `t_*` names strategy_runner logs (t_reord, t_sample,
t_task, t_pre, t_inv, t_sim, t_extract, t_save) so meso trees, macro run.log parses, and
runtime_metrics.db rows all speak one language.

Used by calltree_capture.py / calltree_growth.py; drift-gated by test_calltree_anchors.py.
Not collected by pytest (no test_ prefix).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from contextlib import contextmanager

# ── path anchors ─────────────────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))   # Tests/<sub>/ -> repo root

# Section vocabulary — MUST stay aligned with strategy_runner's checkpoint log line
# (test_calltree_anchors.py pins this against the source).
SECTIONS = ('t_reord', 't_sample', 't_task', 't_pre', 't_inv', 't_sim', 't_extract', 't_save')

# Subtree-root qualname -> section, for full-fidelity captures where sections cannot be
# entered from outside (_run_strategy_worker owns its own loop). Together these partition
# the batch-loop body at strategy_runner.py L489-690.
SECTION_MAP: dict[str, str] = {
    'Capacity_Reloader.reload'   : 't_reord',
    'ReorderMixin.check_reorders': 't_reord',   # Inventory_Manager inherits it from the mixin
    'Batch.__init__'                  : 't_sample',
    'Task.from_batch'                 : 't_task',
    'fused_pre_snapshot'              : 't_pre',
    'snapshot_aisle_metrics'          : 't_pre',
    'save_bin_keyframe'               : 't_pre',
    'DeferredPickSimulation.run'      : 't_sim',
    'extract_batch_stats'             : 't_extract',
    'extract_task_stats'              : 't_extract',
    'extract_picker_events'           : 't_extract',
    'extract_picks'                   : 't_extract',
    'save_batch_stats'                : 't_save',
    'save_task_stats'                 : 't_save',
    'save_picker_events'              : 't_save',
    'save_picks'                      : 't_save',
    'save_bin_placements'             : 't_save',
    'save_bin_evictions'              : 't_save',
    'save_aisle_metrics'              : 't_save',
    'save_reorder_queue'              : 't_save',
    'save_worker_checkpoint'          : 't_save',
}

_DEFAULT_PREFIXES = ('Warehouse', 'Optimization', 'Schema')


class Node:
    """One aggregated call-path node. Children keyed by name for O(1) merge."""
    __slots__ = ('name', 'loc', 'kind', 'calls', 'cum_s', 'self_s', 'children')

    def __init__(self, name: str, loc: str = '', kind: str = 'fn') -> None:
        self.name     = name
        self.loc      = loc
        self.kind     = kind          # 'root' | 'section' | 'fn' | 'ext' | 'threads'
        self.calls    = 0
        self.cum_s    = 0.0
        self.self_s   = 0.0
        self.children: dict[str, 'Node'] = {}

    def child(self, name: str, loc: str = '', kind: str = 'fn') -> 'Node':
        n = self.children.get(name)
        if n is None:
            n = Node(name, loc, kind)
            self.children[name] = n
        return n

    def to_dict(self) -> dict:
        return {
            'name': self.name, 'loc': self.loc, 'kind': self.kind,
            'calls': self.calls,
            'cum_s': round(self.cum_s, 6), 'self_s': round(self.self_s, 6),
            'children': [c.to_dict() for c in
                         sorted(self.children.values(), key=lambda n: -n.cum_s)],
        }


class _ThreadState:
    """Per-thread tracing state: its own subtree + frame stack (merged at stop()).

    Stack entries are (node_or_None, t0, add_time, anchor): `anchor` is the nearest
    project node above (inherited through transparent frames; None at top level, where
    the ambient section node applies)."""
    __slots__ = ('root', 'stack', 'active', 'ambient', 'section')

    def __init__(self, section: str | None) -> None:
        self.root    = Node('<thread>', kind='threads')
        self.stack: list[tuple[Node | None, float, bool, Node | None]] = \
            [(None, 0.0, False, None)]
        self.active: set[int] = set()   # id(node) currently on stack (recursion guard)
        self.ambient: Node | None = None
        self.section = section          # main-thread section active at first event


class CallTreeTracer:
    """Aggregating deterministic tracer. Use: start() / section(name) / stop() / tree()."""

    def __init__(self, include_prefixes: tuple[str, ...] = _DEFAULT_PREFIXES,
                 track_c_calls: bool = True) -> None:
        sep = os.sep
        self._prefixes = tuple(os.path.join(_REPO_ROOT, p) + sep for p in include_prefixes)
        self._track_c  = track_c_calls
        self._root     = Node('__root__', kind='root')
        self._main     = threading.get_ident()
        self._threads: dict[int, _ThreadState] = {}
        self._lock     = threading.Lock()      # only for _threads dict creation
        self._current_section: str | None = None
        self._section_nodes: dict[str, Node] = {}
        self._started  = False

    # ── helpers ──────────────────────────────────────────────────────────────
    def _included(self, filename: str) -> bool:
        return filename.startswith(self._prefixes)

    @staticmethod
    def _fn_name(code) -> tuple[str, str]:
        """(display name, repo-relative loc) for a code object."""
        fn = code.co_filename
        try:
            rel = os.path.relpath(fn, _REPO_ROOT).replace(os.sep, '/')
        except ValueError:      # different drive
            rel = os.path.basename(fn)
        mod = os.path.splitext(os.path.basename(fn))[0]
        return f'{mod}:{code.co_qualname}', f'{rel}:{code.co_firstlineno}'

    def _state(self) -> _ThreadState:
        tid = threading.get_ident()
        st  = self._threads.get(tid)
        if st is None:
            with self._lock:
                st = self._threads.get(tid)
                if st is None:
                    if tid == self._main:
                        st = _ThreadState(None)
                        st.root = self._root
                        st.ambient = self._ambient_node()
                    else:
                        # Worker thread: its own subtree, grafted at stop() under the
                        # section that was active when it first ran.
                        st = _ThreadState(self._current_section)
                        st.ambient = st.root
                    self._threads[tid] = st
        return st

    def _ambient_node(self) -> Node:
        if self._current_section is not None:
            return self._section_nodes[self._current_section]
        return self._root

    # ── the profile callback ─────────────────────────────────────────────────
    def _profile(self, frame, event: str, arg) -> None:
        st = self._state()

        if event == 'call':
            code   = frame.f_code
            top    = st.stack[-1]
            parent = top[3] or st.ambient or st.root
            if self._included(code.co_filename):
                name, loc = self._fn_name(code)
                node      = parent.child(name, loc)
                node.calls += 1
                nid = id(node)
                add = nid not in st.active                 # recursion guard for cum time
                st.active.add(nid)
                st.stack.append((node, time.perf_counter(), add, node))
            else:
                # Transparent frame: count-only leaf, inherit the anchor so project
                # frames beneath (e.g. sort keys, driver bodies) still attach correctly.
                name = f'<ext>:{os.path.basename(code.co_filename)}:{code.co_name}'
                parent.child(name, kind='ext').calls += 1
                st.stack.append((None, 0.0, False, top[3]))

        elif event == 'return':
            if len(st.stack) > 1:
                node, t0, add, _anchor = st.stack.pop()
                if node is not None and add:
                    node.cum_s += time.perf_counter() - t0
                    st.active.discard(id(node))

        elif event == 'c_call' and self._track_c:
            top    = st.stack[-1]
            parent = top[3] or st.ambient or st.root
            mod    = getattr(arg, '__module__', None) or 'builtins'
            name   = f'<c>:{mod}.{getattr(arg, "__qualname__", repr(arg))}'
            parent.child(name, kind='ext').calls += 1
        # c_return / c_exception: nothing to do — C leaves are count-only.

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._started:
            raise RuntimeError('tracer already started')
        self._started = True
        threading.setprofile(self._profile)    # future threads
        sys.setprofile(self._profile)          # this thread

    def stop(self) -> None:
        sys.setprofile(None)
        threading.setprofile(None)
        self._started = False
        # Graft worker-thread trees under their spawn section (merged by path).
        for tid, st in list(self._threads.items()):
            if st.root is self._root:
                continue
            target = (self._section_nodes.get(st.section) or self._root) \
                .child('<threads>', kind='threads')
            _merge(target, st.root)
        self._threads.clear()

    @contextmanager
    def section(self, name: str):
        """Named section root aligned to the t_* vocabulary. Main-thread only."""
        if name not in SECTIONS:
            raise ValueError(f'unknown section {name!r} — use one of {SECTIONS}')
        node = self._section_nodes.get(name)
        if node is None:
            node = self._root.child(name, kind='section')
            self._section_nodes[name] = node
        prev, self._current_section = self._current_section, name
        st = self._threads.get(threading.get_ident())
        prev_amb = st.ambient if st is not None else None
        if st is not None and st.root is self._root:
            st.ambient = node
        t0 = time.perf_counter()
        try:
            yield
        finally:
            node.cum_s += time.perf_counter() - t0
            node.calls += 1
            self._current_section = prev
            if st is not None and st.root is self._root:
                st.ambient = prev_amb if prev_amb is not None else self._ambient_node()

    # ── results ──────────────────────────────────────────────────────────────
    def tree(self) -> Node:
        _finalize(self._root)
        return self._root


# ── tree utilities ───────────────────────────────────────────────────────────

def _merge(dst: Node, src: Node) -> None:
    """Merge src's children into dst by name, summing calls/cum recursively."""
    for name, s in src.children.items():
        d = dst.children.get(name)
        if d is None:
            dst.children[name] = s
        else:
            d.calls += s.calls
            d.cum_s += s.cum_s
            _merge(d, s)


def _finalize(node: Node) -> None:
    for c in node.children.values():
        _finalize(c)
    child_cum = sum(c.cum_s for c in node.children.values())
    node.self_s = max(0.0, node.cum_s - child_cum)


def attribute_sections(root: Node) -> dict[str, float]:
    """Section -> cum_s. Section-kind children win; else SECTION_MAP on topmost match."""
    out = {s: 0.0 for s in SECTIONS}

    def walk(node: Node) -> None:
        for c in node.children.values():
            if c.kind == 'section' and c.name in out:
                out[c.name] += c.cum_s
                continue                       # everything below is already attributed
            qual = c.name.split(':', 1)[-1]
            sec  = SECTION_MAP.get(qual)
            if sec is not None:
                out[sec] += c.cum_s
                continue                       # topmost match wins; don't descend
            walk(c)

    walk(root)
    return out


def counts_fingerprint(root: Node) -> str:
    """sha256 over sorted (path, calls) pairs of PROJECT nodes — O(1) determinism diffs.

    'ext'/'c' leaves are excluded: thread-pool bookkeeping (weakrefs, queue gets, lock
    churn) varies with OS scheduling even under fixed seeds, and the determinism claim
    this fingerprint makes is about the project's own call counts."""
    lines: list[str] = []

    def walk(node: Node, path: str) -> None:
        for c in node.children.values():
            if c.kind == 'ext':
                continue
            p = f'{path}/{c.name}'
            lines.append(f'{p}|{c.calls}')
            walk(c, p)

    walk(root, '')
    lines.sort()
    return hashlib.sha256('\n'.join(lines).encode('utf-8')).hexdigest()


def to_capture_dict(root: Node, *, meta: dict, sections_wall: dict[str, float],
                    wall_untraced: float, wall_traced: float | None) -> dict:
    """Assemble the calltree-v1 capture document (see README / calltree_capture.py)."""
    total = sum(sections_wall.values()) or 1.0
    return {
        'schema': 'calltree-v1',
        'meta': meta,
        'wall_s': {'untraced': round(wall_untraced, 6),
                   'traced': round(wall_traced, 6) if wall_traced is not None else None},
        'sections': [{'name': k, 'wall_s': round(v, 6), 'share': round(v / total, 4)}
                     for k, v in sections_wall.items()],
        'tree': root.to_dict(),
        'counts_fingerprint': counts_fingerprint(root),
    }


def write_capture(doc: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, indent=1)
