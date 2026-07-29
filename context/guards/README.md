# context/guards — checks that run against the *content* of files, not their structure

`context/arch/` verifies that the documented architecture matches the code. `context/memory/`
verifies that the memory store is intact. This directory holds guards on what may be **written
into a tracked file at all**, regardless of which layer it belongs to.

| Module | Enforces |
|---|---|
| `path_guard.py` | no machine-local filesystem path reaches a tracked file or a memory |
| `docref_guard.py` | every `<doc>.md section N` reference still points at a real heading |
| `hook_check.py` | the hook wiring: blocks the write on `PreToolUse`, nags on `Stop` |

## Why the path guard exists

A tracked file is public — it goes to GitHub, and `context/memory/store/` goes with it. A drive
letter, a home directory, or a username in that content leaks the shape of one machine and pins the
repo to it. Three tracked files carried such paths before this guard, and one of them
(`Tests/bench/bench_sections.py`) only worked on a machine with those exact drive letters.

**Legal:** repo-relative paths, `~/`-relative forms, `%USERPROFILE%` / `$HOME`, and the `.env` key
names `COMPARISON_OUTPUT_DIR` / `PROFILE_INPUT_DIR` — which is how a machine path is *supposed* to
be referred to here. The entire verified anchor layer is built from repo-relative paths;
`context/files.yml` alone holds 167 of them.

**Forbidden:** drive-letter absolutes (plain or `\\`-escaped), home-directory absolutes, UNC shares,
the current username, and session-scoped scratchpad paths.

```bash
python context/guards/path_guard.py --scan            # all tracked files; exit 1 on findings
python context/guards/path_guard.py --scan PATH ...   # just these
```

## Why the docref guard exists

Prose across the repo points at numbered sections — "see CLAUDE.md section 5", "CLAUDE.md §2 is
canonical". Those are anchors exactly like the `name@file` anchors in `context/`, and they rot the
same silent way: renumber or retitle a heading and every reference still *looks* right, so it
survives review while sending the reader somewhere wrong. There were 14 of them, spread across
`.py`, `.md` and the memory mirror, correct only because they had just been checked by hand.

It resolves numbered references against the headings actually present, and word references
(`§Conventions`) against heading text. A bare filename mention is not a reference and is ignored.
The `Stop` hook runs it only when a Markdown file changed — which is the only time it can break.

```bash
python context/guards/docref_guard.py --scan
```

## Two rules this code follows, and must keep following

**The guard never hardcodes the username.** It derives it at runtime from the home directory.
Writing it down would make this file the first violation of its own rule — and would leak the name
into git permanently. Every example here and in the tests uses a synthetic value.

**The blocking hook fails open.** `hook_check.py --pre-write` is the only hook in this repo allowed
to fail a call. Any internal error, unreadable payload, or missing dependency exits 0 and permits
the write: a crashing guard must never brick every write in a session. It is a floor, not a
sole line of defence — the `Stop` scan and `--scan` catch what it misses.

## Does NOT belong here

Structural checks on the architecture (`context/arch/`), memory-store integrity
(`context/memory/`), or anything that needs the call graph. A guard here reads file *text* and
answers one yes/no question about its content.

## Tuning `ALLOW`

`ALLOW` in `path_guard.py` maps a repo-relative path to the reason the guard is switched off for
it. It is declared and reasoned, exactly as `context/architecture.yml` declares intent — never a
silent skip. Keep it near-empty; it currently holds one entry (`.claude/settings.json`, whose
permission entries must name real paths in order to match against them). Prefer fixing the file.

Note the two patterns that earn their complexity, both against real content in this repo: the
separator is `[\\/]{1,2}` because notebooks and `nodes.json` store paths JSON-escaped, and a
minimum segment length rejects `db:\n\n…` — a JSON-escaped newline in a docstring, which would
otherwise block every write to `context/arch/nodes.json` forever.
