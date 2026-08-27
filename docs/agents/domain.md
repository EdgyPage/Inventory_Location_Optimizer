# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This repo is **single-context**: one `CONTEXT.md` and one `docs/adr/` at the repo root. There is no
`CONTEXT-MAP.md` and no per-package context.

> **Name collision, read this once.** `CONTEXT.md` (domain vocabulary, repo root) is unrelated to the
> existing `context/` directory (verified `name@file` anchors, flows, `architecture.yml`, `INDEX.md`).
> Different purpose, similar name. Before touching anything under `context/`, read `context/INDEX.md`
> — that is a separate, verifier-backed system with its own gates.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If either doesn't exist, **proceed silently**. Don't flag their absence; don't suggest creating them
upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and
`/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-<slug>.md
│   └── 0002-<slug>.md
├── Warehouse/          domain engine
└── Optimization/       run harness
```

If this repo ever splits into genuinely separate contexts, the multi-context layout is a root
`CONTEXT-MAP.md` pointing at one `CONTEXT.md` per context, with context-scoped `docs/adr/`
directories beside them. Not the case today.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test
name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language
the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_

An ADR is a decision record, not a design doc. Longer engineering write-ups in this repo live in
`docs/design/` and are excluded from the built site; keep ADRs short and decision-shaped.
