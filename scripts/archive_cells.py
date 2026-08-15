"""archive_cells.py — move completed what-if cells off the hot drive while the run continues.

A finished cell is ~99.8 % SQLite: the sim DBs and their keyframe siblings. The hot drive cannot
hold a whole sweep, so each cell has to move to slower bulk storage as soon as it is done — without
disturbing the simulation, and without breaking anything that reads the cell later.

Two facts shape the whole design:

  * A cell is quiescent the moment its scenario returns. Cells run strictly sequentially and no
    handle outlives an arm (every SQLite connection is opened and closed inside a save call; the
    only long-lived files, run.log and runtime_metrics.db, live at the RUN ROOT, not in a cell).
  * But analysis runs AFTER every cell and opens each sim DB by the ABSOLUTE path recorded in
    sim_meta.json, and the cross-cell what-if scans need every cell at once. So a plain move
    breaks the run.

The resolution is a Windows directory junction. Python does not classify a junction as a link --
os.path.islink is False, is_dir() is True -- and this repo contains no follow_symlinks=False,
islink, or scandir call anywhere. So every walker and every absolute db_path keeps working through
it, unchanged. The cell's bytes are on the cold drive; its path is not.

Because the cold drive is slow, this analyses each cell BEFORE moving it: the heavy per-arm reads
happen while the data is still local, and only the light cross-cell scan (one aggregate query per
DB) ever crosses to bulk storage. Run the simulation with --no-analyze and let this drive analysis.

Configuration: COLD_DRIVE in .env, the same file that holds COMPARISON_OUTPUT_DIR. Importing the
sim config injects it into the environment; nothing here hardcodes a location.

    python scripts/archive_cells.py --run RUN --dry-run
    python scripts/archive_cells.py --run RUN --watch          # loop while the sim runs
    python scripts/archive_cells.py --run RUN --cell k1_off_rr
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
if _ROOT not in sys.path:                       # entry-script bootstrap
    sys.path.insert(0, _ROOT)

STAGING_SUFFIX = '.__archiving'                 # transient; never a hole where a cell used to be
RETRY_ERRNOS = (5, 32)                          # WinError 5 / 32: AV or the indexer holding a file


def cold_root() -> str | None:
    """The configured bulk-storage root, or None.

    None rather than a fallback path on purpose: a silent fallback is how a run ends up writing
    hundreds of gigabytes somewhere nobody intended.
    """
    from Optimization.config import sim_config as sc     # noqa: F401 - loads .env into os.environ
    val = (os.environ.get('COLD_DRIVE') or '').strip()
    if val.startswith(('r"', "r'")):
        val = val[2:].rstrip('"').rstrip("'")
    val = val.strip('"').strip("'")
    return val or None


# ── completeness ────────────────────────────────────────────────────────────────────────────
def cell_state(rt, cell: str, layout: dict, mixed: bool) -> tuple[bool, str]:
    """(is_complete, why). Stricter than the driver's own resume probe.

    The driver's `_cell_complete` accepts one sim_meta.json per pair, so a cell with two of eight
    leaves finalized reports complete. Archiving on that would move a half-written cell.
    """
    pairs = list(layout.get('pairs') or [])
    cfgs = layout.get('configs') or {}
    n_cfg = len(cfgs.get('store') or []) + (len(cfgs.get('fulfillment') or []) if mixed else 0)
    want = len(pairs) * n_cfg
    if not want:
        return False, 'cannot derive the expected leaf count from run_layout.json'
    leaves = [(c, cr) for c, cr in rt.channel_runs(cell)]
    finalized = [cr for _c, cr in leaves if os.path.isfile(rt.sim_meta(cr))]
    if len(finalized) < want:
        return False, f'{len(finalized)}/{want} leaves finalized'
    stray = _in_flight(rt, cell)
    if stray:
        return False, f'{len(stray)} in-flight file(s) still present (e.g. {stray[0]})'
    return True, f'{len(finalized)}/{want} leaves finalized'


def _in_flight(rt, cell: str) -> list:
    """resume.pkl / _ckpt_*.pkl are removed at finalize; their presence means an arm is live.

    The names come from the CONTRACT (`resume_pkl` / `checkpoint_pkl`), not from hardcoded
    literals: preflight declared both transients, so renaming either in schema.py moves this
    probe with it instead of silently blinding it.  Returned as sorted cell-relative paths, the
    same shape the old cell walk produced.
    """
    cell_dir = rt.cell_dir(cell)
    hits = rt.glob('resume_pkl', cell=cell) + rt.glob('checkpoint_pkl', cell=cell)
    return sorted(os.path.relpath(p, cell_dir) for p in dict.fromkeys(hits))


def archivable(rt, layout: dict, mixed: bool, include_last: bool) -> list:
    """Complete cells that are also safe to touch.

    Cells run sequentially, so a cell is provably not the live one once a LATER cell has started.
    The final cell only becomes safe when the run itself is over, which the caller asserts with
    include_last.
    """
    names = [n for n, _d in rt.cells()]
    out = []
    for i, name in enumerate(names):
        if is_archived(rt.cell_dir(name)):
            continue
        later_started = any(os.path.isdir(rt.cell_dir(n)) for n in names[i + 1:])
        if not later_started and not include_last:
            continue
        ok, why = cell_state(rt, name, layout, mixed)
        if ok:
            out.append((name, why))
    return out


def is_archived(cell_dir: str) -> bool:
    """True if this path is already a junction (Python reports a junction as a plain dir)."""
    if not os.path.isdir(cell_dir):
        return False
    try:
        os.readlink(cell_dir)
        return True
    except OSError:
        return False


# ── the move ────────────────────────────────────────────────────────────────────────────────
# SQLite's transient companions.  They are deliberately NOT copied into an archive (see
# _sweep_sidecars), so any count used to compare a source tree against its copy must agree on
# whether they are in it — see the guard in archive_cell().
_SIDECAR_SUFFIXES = ('-wal', '-shm')


def _tree_stats(root: str, *, skip_sidecars: bool = False) -> tuple[int, int]:
    n = total = 0
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            if skip_sidecars and fn.endswith(_SIDECAR_SUFFIXES):
                continue
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
                n += 1
            except OSError:
                pass
    return n, total


def copy_verified(src_root: str, dst_root: str, *, resume: bool = True, echo=print) -> dict:
    """Copy a tree file-by-file, verifying each file as it lands. Resumable, and never destructive.

    shutil.copytree is all-or-nothing: an interrupted copy leaves a partial tree that the next
    attempt refuses to touch, and it verifies nothing. That is the wrong shape for a multi-hundred-
    gigabyte move onto an external disk, where a disconnect mid-copy is a routine failure rather
    than an exceptional one.

    Each file is copied, size-checked, and (if SQLite) integrity-checked before the next one
    starts, so an interruption leaves a tree where every file present is known-good. Re-running
    skips those and continues.
    """
    stats = {'copied': 0, 'skipped': 0, 'bytes': 0, 'problems': []}
    plan = []
    for dirpath, _d, files in os.walk(src_root):
        rel = os.path.relpath(dirpath, src_root)
        for fn in files:
            s = os.path.join(dirpath, fn)
            d = os.path.join(dst_root, fn) if rel == '.' else os.path.join(dst_root, rel, fn)
            try:
                plan.append((s, d, os.path.getsize(s)))
            except OSError as exc:
                stats['problems'].append(f'cannot stat {fn}: {exc}')
    total = sum(sz for _s, _d, sz in plan)
    echo(f'    {len(plan)} file(s), {total / 1024 ** 3:.1f} GB')

    free = shutil.disk_usage(_existing_ancestor(dst_root)).free
    if free < total * 1.02:
        stats['problems'].append(
            f'destination has {free / 1024 ** 3:.0f} GB free, needs {total / 1024 ** 3:.0f} GB')
        return stats

    last_echo = time.time()
    for i, (s, d, sz) in enumerate(plan, 1):
        try:
            if resume and os.path.isfile(d) and os.path.getsize(d) == sz:
                if not d.endswith('.db') or not _quick_check(d):
                    stats['skipped'] += 1
                    continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            _retry(lambda a=s, b=d: shutil.copy2(a, b))
            if os.path.getsize(d) != sz:
                raise OSError(f'size mismatch after copy ({os.path.getsize(d)} != {sz})')
            if d.endswith('.db'):
                bad = _quick_check(d)
                if bad:
                    raise OSError(bad)
            stats['copied'] += 1
            stats['bytes'] += sz
        except (OSError, shutil.Error) as exc:
            # Remove the bad partial so a resume re-copies it rather than trusting it.
            try:
                if os.path.isfile(d):
                    os.remove(d)
            except OSError:
                pass
            stats['problems'].append(f'{os.path.relpath(s, src_root)}: {exc}')
            if len(stats['problems']) >= 25:
                stats['problems'].append('too many failures — stopping')
                break
        if time.time() - last_echo > 30:
            echo(f'      {i}/{len(plan)}  {stats["bytes"] / 1024 ** 3:.1f} GB copied')
            last_echo = time.time()
    return stats


def _existing_ancestor(path: str) -> str:
    while path and not os.path.isdir(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path or '.'


def evacuate(src: str, dst: str, *, delete_source: bool = False, dry_run: bool = False,
             echo=print) -> dict:
    """Move a whole tree to bulk storage — for emptying a drive, not for mid-run archival.

    No junction is left behind: this exists for the case where the source volume is about to be
    reformatted, so a link back to it would be pointless. That also means the caller is responsible
    for repointing anything that referenced the old location.
    """
    res = {'src': os.path.basename(src), 'status': 'dry-run'}
    if not os.path.isdir(src):
        return {**res, 'status': 'failed', 'error': 'source not found'}
    n, total = _tree_stats(src)
    res.update(files=n, gb=round(total / 1024 ** 3, 2))
    echo(f'  {os.path.basename(src)}: {n} file(s), {total / 1024 ** 3:.1f} GB')
    if dry_run:
        return res

    stats = copy_verified(src, dst, echo=echo)
    res['copied'], res['skipped'] = stats['copied'], stats['skipped']
    if stats['problems']:
        res['status'] = 'failed'
        res['problems'] = stats['problems'][:10]
        echo(f'    FAILED ({len(stats["problems"])} problem(s)) — source left untouched')
        for p in stats['problems'][:5]:
            echo(f'      {p}')
        return res

    # Independent post-check: the copy loop verified each file as it went, but a second pass over
    # the finished tree is what catches a file that vanished from the source mid-copy.
    problems = _verify_copy(src, dst, echo)
    if problems:
        res['status'] = 'failed'
        res['problems'] = problems[:10]
        echo(f'    POST-VERIFY FAILED ({len(problems)}) — source left untouched')
        return res

    if delete_source:
        try:
            _retry(lambda: shutil.rmtree(src))
            echo(f'    source removed; {total / 1024 ** 3:.1f} GB freed')
        except OSError as exc:
            res['status'] = 'copied-not-deleted'
            res['error'] = f'copy verified but source could not be removed: {exc}'
            echo(f'    {res["error"]}')
            return res
    else:
        echo('    copy verified; source KEPT (pass --delete-source to free the space)')
    res['status'] = 'evacuated'
    return res


def _verify_copy(src: str, dst: str, echo) -> list:
    """Every file present at the same size, and every SQLite file structurally intact.

    The integrity pass is the reason this is safe to delete after: a truncated copy from a
    disconnected external drive has the right name and the wrong contents.
    """
    problems = []
    checked_db = 0
    for dirpath, _d, files in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        for fn in files:
            s = os.path.join(dirpath, fn)
            d = os.path.join(dst, rel, fn) if rel != '.' else os.path.join(dst, fn)
            if not os.path.isfile(d):
                problems.append(f'missing in copy: {os.path.join(rel, fn)}')
                continue
            if os.path.getsize(s) != os.path.getsize(d):
                problems.append(f'size differs: {os.path.join(rel, fn)}')
                continue
            if fn.endswith('.db'):
                bad = _quick_check(d)
                checked_db += 1
                if bad:
                    problems.append(f'{os.path.join(rel, fn)}: {bad}')
    echo(f'    verified {checked_db} sqlite file(s)')
    return problems


def _quick_check(db_path: str) -> str | None:
    """Integrity-check a copied SQLite file WITHOUT touching the directory it lives in.

    `immutable=1` is load-bearing, not a micro-optimisation. These DBs are written in WAL mode, so
    a plain read-only open makes SQLite create `-wal` and `-shm` sidecars next to the file. Doing
    that while verifying an archive adds two files per DB to the destination — 548 extra files for
    a 274-DB cell — which silently inflated the copy and broke the count check that follows.
    `immutable=1` promises the file cannot change, so SQLite skips the sidecars entirely.
    """
    import pathlib
    uri = pathlib.Path(db_path).as_uri() + '?mode=ro&immutable=1'
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return f'cannot open: {exc}'
    try:
        row = con.execute('PRAGMA quick_check').fetchone()
        return None if row and row[0] == 'ok' else f'quick_check: {row}'
    except sqlite3.Error as exc:
        return f'quick_check failed: {exc}'
    finally:
        con.close()


def _sweep_sidecars(root: str) -> int:
    """Remove any -wal/-shm left beside a copied DB. Belt and braces behind immutable=1.

    A stray sidecar is not just clutter: it makes the archived copy differ from the original, and
    on a later read SQLite would try to recover from a WAL that describes nothing.
    """
    removed = 0
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            if fn.endswith(_SIDECAR_SUFFIXES):
                try:
                    os.remove(os.path.join(dirpath, fn))
                    removed += 1
                except OSError:
                    pass
    return removed


def _retry(fn, attempts: int = 5, delay: float = 2.0):
    for i in range(attempts):
        try:
            return fn()
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in RETRY_ERRNOS or i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


def supports_reparse(directory: str) -> bool:
    """Can this volume hold a directory junction?

    exFAT cannot — it has no reparse points — and an external SSD formatted for portability is very
    often exFAT. Creating the junction is the only reliable test: the failure is a bare
    OSError(22, 'Incorrect function') from the FSCTL, and it leaves an empty directory behind that
    must be cleaned up. Probing here costs milliseconds; discovering it after copying a hundred
    gigabytes does not.
    """
    import _winapi
    probe = os.path.join(directory, f'.__reparse_probe_{os.getpid()}')
    target = os.path.join(directory, f'.__reparse_target_{os.getpid()}')
    try:
        os.makedirs(target, exist_ok=True)
        _winapi.CreateJunction(target, probe)
        return is_archived(probe)
    except OSError:
        return False
    finally:
        for p in (probe, target):
            try:
                os.rmdir(p)                      # link or empty dir; never recursive
            except OSError:
                pass


def archive_keyframes(rt, cell: str, cold: str, *, dry_run: bool = False, echo=print) -> dict:
    """Move only the keyframe DBs — the one family with no downstream reader.

    This is what remains possible when the hot volume cannot hold a junction. Keyframes are ~19% of
    a cell and are read by NOTHING in the analysis path: not run_analysis, not the rollup, not
    either what-if scan. The replay viewer is the only consumer and it already degrades to an empty
    string when the file is absent. So they can simply leave, with no link and no path rewriting.
    """
    src_cell = rt.cell_dir(cell)
    dst_cell = os.path.join(cold, os.path.basename(rt.base), cell)
    moved = total = 0
    plan = []
    for dirpath, _d, files in os.walk(src_cell):
        for fn in files:
            if fn.endswith('.keyframes.db'):
                s = os.path.join(dirpath, fn)
                rel = os.path.relpath(s, src_cell)
                plan.append((s, os.path.join(dst_cell, rel), os.path.getsize(s)))
    gb = sum(sz for _s, _d, sz in plan) / (1024 ** 3)
    echo(f'  {cell}: {len(plan)} keyframe DB(s), {gb:.1f} GB')
    if dry_run:
        return {'cell': cell, 'mode': 'keyframes', 'files': len(plan), 'gb': round(gb, 2),
                'status': 'dry-run'}
    for s, d, sz in plan:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        if os.path.getsize(d) != sz:
            return {'cell': cell, 'mode': 'keyframes', 'status': 'failed',
                    'error': f'size mismatch after copy: {os.path.basename(d)}'}
        bad = _quick_check(d)
        if bad:
            os.remove(d)
            return {'cell': cell, 'mode': 'keyframes', 'status': 'failed',
                    'error': f'{os.path.basename(d)}: {bad}'}
        _retry(lambda p=s: os.remove(p))
        moved += 1
        total += sz
    echo(f'    moved {moved} file(s), {total / (1024 ** 3):.1f} GB freed')
    return {'cell': cell, 'mode': 'keyframes', 'files': moved,
            'gb': round(total / (1024 ** 3), 2), 'status': 'archived'}


def archive_cell(rt, cell: str, cold: str, *, dry_run: bool = False, echo=print) -> dict:
    """Copy one cell to bulk storage, verify it, then replace it with a junction.

    Ordering is the safety property: the source is only ever renamed aside AFTER a verified copy
    exists, and it is only deleted AFTER the junction is in place and proven to read. There is no
    window in which the cell path does not resolve -- which matters because the driver's resume
    probe treats a missing directory as "never ran" and would re-simulate the whole cell.
    """
    src = rt.cell_dir(cell)
    dst = os.path.join(cold, os.path.basename(rt.base), cell)
    n, total = _tree_stats(src)
    gb = total / (1024 ** 3)
    res = {'cell': cell, 'files': n, 'gb': round(gb, 2), 'status': 'dry-run'}
    echo(f'  {cell}: {n} file(s), {gb:.1f} GB -> {os.path.basename(cold)}')
    if dry_run:
        return res

    if os.path.exists(dst):
        res['status'] = 'skipped'
        res['error'] = 'destination already exists'
        echo('    destination already exists — refusing to overwrite')
        return res

    t0 = time.time()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst)
    echo(f'    copied in {(time.time() - t0) / 60:.1f} min')

    problems = _verify_copy(src, dst, echo)
    stray = _sweep_sidecars(dst)
    if stray:
        echo(f'    removed {stray} stray WAL/SHM sidecar(s) from the copy')
    if problems:
        res['status'] = 'failed'
        res['problems'] = problems[:10]
        echo(f'    VERIFY FAILED ({len(problems)}) — leaving the original in place')
        for p in problems[:5]:
            echo(f'      {p}')
        return res

    # Re-measure BOTH sides after verification rather than trusting the pre-copy count of the
    # source: verification itself can add files, and the junction is about to be compared to this.
    #
    # Both counts must EXCLUDE sidecars.  `n` above was taken before `_sweep_sidecars(dst)`
    # deliberately removed them from the copy, so comparing against it made `n_dst` short by
    # exactly the number swept — and every cell that carries a sidecar carries hundreds.  That
    # made this guard reject every such cell as a short copy ("copy has 4343 files, source had
    # 4621"), which is to say: any cell touched by a reader could never be archived at all.  The
    # guard is asking "did everything that must survive the swap land?", and a sidecar is
    # explicitly not in that set.
    n_dst,  _sz_dst = _tree_stats(dst, skip_sidecars=True)
    n_kept, _sz_src = _tree_stats(src, skip_sidecars=True)
    if n_dst != n_kept:
        res['status'] = 'failed'
        res['error'] = (f'copy has {n_dst} files, source had {n_kept} '
                        f'(sidecars excluded from both) — refusing to swap in a junction')
        echo(f'    {res["error"]}')
        return res

    staged = src + STAGING_SUFFIX
    _retry(lambda: os.rename(src, staged))          # instant: same volume
    try:
        import _winapi
        _winapi.CreateJunction(dst, src)            # (target, link)
        if not os.path.isdir(src) or not os.readlink(src):
            raise OSError('junction did not resolve')
        # `src` now RESOLVES TO `dst`, so this must expect the copy's count (n_dst), not the
        # pre-copy source count `n` — which still includes the sidecars swept out of the copy.
        seen, _sz = _tree_stats(src, skip_sidecars=True)
        if seen != n_dst:
            raise OSError(f'junction reads {seen} file(s), expected {n_dst}')
    except Exception as exc:
        # A failed CreateJunction leaves an EMPTY REAL DIRECTORY behind, not a link — so testing
        # for a junction here is not enough, and skipping the cleanup makes the rename below fail
        # with FileExistsError, masking the real error.
        if os.path.isdir(src):
            try:
                os.rmdir(src)                        # link or empty dir; never recursive
            except OSError:
                pass
        if not os.path.exists(src):
            os.rename(staged, src)                   # put the original back
        shutil.rmtree(dst, ignore_errors=True)
        res['status'] = 'failed'
        res['error'] = (f'junction step failed: {exc!r}. Original left at '
                        f'{os.path.basename(src)}{"" if not os.path.exists(staged) else STAGING_SUFFIX}')
        echo(f'    {res["error"]}')
        return res

    _retry(lambda: shutil.rmtree(staged))            # a real directory; the junction is untouched
    res['status'] = 'archived'
    res['seconds'] = round(time.time() - t0, 1)
    echo(f'    archived; {gb:.1f} GB freed, path still resolves')
    return res


def analyse_cell(rt, cell: str, workers: int, echo=print) -> bool:
    """Run this cell's analysis while it is still on fast storage."""
    cell_dir = rt.cell_dir(cell)
    cmd = [sys.executable, '-m', 'Optimization.run_analysis', cell_dir, '--workers', str(workers)]
    echo(f'  analysing {cell} (workers={workers})')
    env = dict(os.environ, PYTHONIOENCODING='utf-8', MPLBACKEND='Agg')
    p = subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=env)
    if p.returncode != 0:
        echo(f'    analysis exited {p.returncode} — NOT archiving this cell')
        return False
    try:
        from Optimization import run_channel_rollup
        run_channel_rollup.rollup(cell_dir, log=lambda *_a, **_k: None)
    except Exception as exc:
        echo(f'    rollup failed: {exc!r} — NOT archiving this cell')
        return False
    return True


# ── driver ──────────────────────────────────────────────────────────────────────────────────
def sweep(run_dir: str, *, cold: str, workers: int, analyse: bool, dry_run: bool,
          include_last: bool, only: list | None, mode: str = 'auto', echo=print) -> list:
    from Optimization import runschema
    rt = runschema.resolver_for(run_dir)
    layout = rt.layout
    mixed = bool(layout.get('channels') and len(layout['channels']) > 1)
    todo = archivable(rt, layout, mixed, include_last)
    if only:
        todo = [(c, w) for c, w in todo if c in only]
    if not todo:
        return []
    out = []
    for cell, why in todo:
        echo(f'cell {cell}: {why}')
        if analyse and not dry_run and not analyse_cell(rt, cell, workers, echo):
            out.append({'cell': cell, 'status': 'analysis-failed'})
            continue
        if mode == 'keyframes':
            out.append(archive_keyframes(rt, cell, cold, dry_run=dry_run, echo=echo))
        else:
            out.append(archive_cell(rt, cell, cold, dry_run=dry_run, echo=echo))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='Archive completed what-if cells to bulk storage, leaving a junction behind.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('--run', default=None, metavar='DIR',
                    help='run root (absolute, or a name under COMPARISON_OUTPUT_DIR)')
    ap.add_argument('--evacuate', default=None, metavar='SRC',
                    help='move an entire tree to COLD_DRIVE and stop — for emptying a drive before '
                         'reformatting it. Accepts a path, or the keywords "profiles" (the '
                         'simulator input catalogues) and "outputs" (every completed run).')
    ap.add_argument('--to', default=None, metavar='DIR',
                    help='destination for --evacuate (default: mirror the name under COLD_DRIVE)')
    ap.add_argument('--delete-source', action='store_true',
                    help='with --evacuate: remove the source AFTER the copy verifies')
    ap.add_argument('--cell', action='append', default=None, help='only this cell (repeatable)')
    ap.add_argument('--watch', action='store_true', help='keep polling while the simulation runs')
    ap.add_argument('--interval', type=int, default=300, metavar='S', help='poll interval')
    ap.add_argument('--workers', type=int, default=8, help='analysis pool size')
    ap.add_argument('--no-analyze', action='store_true',
                    help='archive without analysing first (only if analysis already ran)')
    ap.add_argument('--include-last', action='store_true',
                    help='also archive the final cell — only once the simulation has exited')
    ap.add_argument('--mode', default='auto', choices=('auto', 'cell', 'keyframes'),
                    help="'cell' moves everything and leaves a junction (needs a hot volume that "
                         "supports reparse points); 'keyframes' moves only the keyframe DBs, which "
                         "nothing downstream reads; 'auto' picks based on the volume")
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    from Optimization import runschema
    cold = cold_root()
    if not cold:
        print('COLD_DRIVE is not set. Add it to .env alongside COMPARISON_OUTPUT_DIR.')
        return 2
    if not os.path.isdir(cold):
        print('COLD_DRIVE is set but does not resolve to a directory — is the drive connected?')
        return 2

    if a.evacuate:
        from Optimization.config import sim_config as sc
        src = {'profiles': sc._DEFAULT_PROFILES_DIR,
               'outputs': sc._OUTPUT_DIR}.get(a.evacuate, a.evacuate)
        if not os.path.isdir(src):
            print(f'nothing to evacuate at: {a.evacuate}')
            return 2
        dst = a.to or os.path.join(cold, os.path.basename(os.path.normpath(src)))
        if os.path.normcase(os.path.abspath(dst)).startswith(
                os.path.normcase(os.path.abspath(src))):
            print('REFUSING: the destination is inside the source.')
            return 2
        print(f'evacuate : {os.path.basename(os.path.normpath(src))} -> '
              f'{os.path.basename(os.path.normpath(dst))}')
        r = evacuate(src, dst, delete_source=a.delete_source, dry_run=a.dry_run)
        print(f'\n{r["status"]}')
        return 0 if r['status'] in ('evacuated', 'dry-run') else 1

    if not a.run:
        print('pass --run DIR (or --evacuate SRC)')
        return 2
    run_dir = runschema.resolve_base_dir(a.run)
    if not os.path.isdir(run_dir):
        print(f'run directory not found: {a.run}')
        return 2

    free_gb = shutil.disk_usage(cold).free / (1024 ** 3)
    print(f'run  : {os.path.basename(run_dir)}')
    print(f'cold : {free_gb:.0f} GB free')

    # Decide the mode BEFORE copying anything. Whole-cell archival depends on being able to leave a
    # junction where the cell was: without one, the driver's resume probe sees a missing directory
    # and re-simulates the entire cell, and analysis cannot find the DBs it recorded by absolute
    # path. If the hot volume cannot hold a junction, whole-cell archival is simply not available.
    can_link = supports_reparse(run_dir)
    mode = a.mode
    if mode == 'auto':
        mode = 'cell' if can_link else 'keyframes'
    if mode == 'cell' and not can_link:
        print('\nREFUSING: --mode cell needs a junction, and this run directory is on a volume that '
              'does not support reparse points (exFAT cannot). Whole-cell archival would leave a '
              'hole, which makes the run un-resumable and breaks analysis.')
        print('  Use --mode keyframes (moves the ~19% nothing downstream reads), or move the run '
              'output to an NTFS volume.')
        return 2
    print(f'mode : {mode}' + ('' if can_link else '   (hot volume has no reparse-point support)'))

    kw = dict(cold=cold, workers=a.workers, analyse=not a.no_analyze,
              dry_run=a.dry_run, include_last=a.include_last, only=a.cell, mode=mode)
    if not a.watch:
        done = sweep(run_dir, **kw)
        print(f'\n{len(done)} cell(s) processed' if done else '\nno cell is ready to archive')
        return 0 if all(d.get('status') in ('archived', 'dry-run') for d in done) else 1

    print(f'watching every {a.interval}s; Ctrl-C to stop')
    try:
        while True:
            done = sweep(run_dir, **kw)
            for d in done:
                print(f'  -> {d["cell"]}: {d["status"]}')
            time.sleep(a.interval)
    except KeyboardInterrupt:
        print('\nstopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
