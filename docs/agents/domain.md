# Domain Docs

This repository uses a single-context domain-document layout.

## Before exploring

Read:

- `CONTEXT.md` for the domain vocabulary.
- Relevant ADRs under `docs/adr/`.

If either location is absent, proceed silently. Domain documentation is created
lazily when terminology or a durable architecture decision is resolved.

## Layout

```text
/
├── CONTEXT.md
├── docs/
│   ├── agents/
│   │   ├── domain.md
│   │   ├── issue-tracker.md
│   │   └── triage-labels.md
│   └── adr/
└── src/
```

## Vocabulary

Use canonical terms from `CONTEXT.md` in issue titles, specifications,
architecture proposals, and test descriptions.

Do not replace glossary terms with synonyms that `CONTEXT.md` explicitly
avoids. If a required concept is absent, reconsider whether the term belongs to
the domain or record the gap for domain modeling.

## Architecture decisions

Read ADRs relevant to the area being changed. If a proposal contradicts an
accepted ADR, identify the conflict explicitly rather than silently overriding
the decision.
