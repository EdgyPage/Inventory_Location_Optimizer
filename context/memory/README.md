# context/memory — the durable memory layer

Claude Code keeps this project's cross-session memories in a directory **outside the repo**, named
after the repo's absolute path. That works until the repo moves: the slug changes, a fresh empty
store is created, and the old memories are orphaned with no backup and no history. This repo has
already moved once — `.claude/settings.json` carried permission entries naming its previous
location until commit `082b758`.

`store/` is a git-tracked mirror of that live store. Putting it in git **is** the durability
mechanism: three-way merge, `git diff` review, history, and recovery all come free.

| File | Role |
|---|---|
| `store/` | the mirror — one `.md` per memory plus `MEMORY.md`, byte-identical to the live store |
| `sync.py` | `--push` (live → mirror), `--restore` (the manual inverse), `--status` |
| `verify_memory.py` | the contract: 7 checks, exit 0 when clean |
| `hook_check.py` | the advisory wrapper wired to SessionStart / Stop / PreCompact |

## Direction is live → mirror, always

Claude Code owns the live store; it is what the model actually reads. The mirror is a replica.
`--push` copies forward and **refuses if any memory contains a machine-local path**, because a leak
into git history cannot be taken back. `--restore` is for after a move or a fresh clone and refuses
to overwrite a non-empty live store without `--force`.

| Divergence | Meaning | What happens |
|---|---|---|
| in live, not mirror | mirror is behind | `--push` adds it |
| in mirror, not live, live store **non-empty** | a real deletion | `--push` removes it; the diff is the review |
| in mirror, not live, live store **empty/absent** | **the repo moved** | `--push` refuses and prints the `--restore` command |
| same name, different bytes | live is newer by construction | `--push` overwrites; `git diff store/` before committing |

A directory junction from the live store into the repo was rejected: it breaks on a fresh clone,
and `git checkout <old-sha>` would silently rewrite the model's live memories.

## The check that rots on its own

Checks 1–6 (located, parity, index, shape, links, paths) catch mistakes made *now*. Check 7,
**stale anchors**, catches damage done elsewhere: a refactor that moves files silently invalidates
every memory that cites them, and nothing else in the repo would notice. The 2026-07 restructure
staled 8 anchors across 4 of 8 memories — that is what prompted this layer. Findings come with a
candidate: `Warehouse/fast_pick.py -> Warehouse/picking/fast_pick.py ?`

A dangling `[[wiki-link]]` is reported as a **note, not a failure** — the memory convention allows
a forward reference as a marker for something worth writing later, and failing on it would push
against linking liberally.

## What belongs in a memory

Only what has no home in the repo:

- a decision and the **rejected** alternative, with the measurement that killed it — a repo shows
  what exists, never what was tried and abandoned (`gpu-broker-dormant-not-for-placement.md`)
- machine or environment facts that cannot be committed (`results-drive-location.md`)
- the user's working preferences
- an approved plan still in flight
- traps about the **agent's own tooling**, not the repo's

Anything about how to run something, a convention, the layout, or a trap visible in the code goes
in **`CLAUDE.md`**. A verifiable `name@file` anchor goes in **`context/`**. Behaviour of one file
goes in its **docstring**. What may live in a directory goes in that **README**. Writing it in two
places creates two things to rot.

## Does NOT belong here

Repo documentation of any kind, and anything a verifier could assert directly against the code —
that is `context/arch/`'s job. Note `context/` is outside `CATALOG_ROOTS`
(`context/arch/extract.py`), so files here need no `purpose` entry in `context/files.yml`.

```bash
python context/memory/sync.py --status
python context/memory/sync.py --push --dry-run
python context/memory/verify_memory.py              # full, needs the live store
python context/memory/verify_memory.py --repo-only  # mirror only; works in any clone
```
