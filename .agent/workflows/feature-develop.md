---
description: Create a feature branch and develop a new feature following git-workflow.md
---

# Feature Branch Development Workflow

This workflow guides you through the complete lifecycle of developing a change
on a dedicated branch.

> [!IMPORTANT]
> **Agent Rules Compliance**: During development, you MUST strictly follow ALL rules defined in `.agent/rules/`:
> - `python-style.md` - Code style (f-strings, naming, sorting, logging)
> - `python-docs.md` - Documentation (Google Style docstrings)
> - `tech-stack.md` - Technology choices (Python 3.14+, typing, filesystem, async stack)
> - `testing.md` - Test structure and mocking strategy
> - `architecture-context.md` - Library architecture and repository boundaries
> - `git-workflow.md` - This workflow itself

## Step 1: Start Clean
Ensure you're working from the latest `main` branch.

```bash
git checkout main
git pull
```

## Step 2: Create Feature Branch
Use a short descriptive branch name with the `feature/` prefix unless the task is
clearly a fix or docs-only change and another prefix is more appropriate.

**Naming Convention:**
- Use kebab-case (lowercase with hyphens)
- Be specific: `feature/add-user-authentication` not `feature/auth`
- Keep it short but meaningful

```bash
git checkout -b feature/<generated-name>
```

## Step 3: Develop (The Loop)
Iterate on the change with frequent validation.

### A. Write Code
- Modify the intended files in `src/`, `tests/`, `docs/`, or `.agent/`
- Add or update tests when behavior changes
- Keep public docs in sync when API, CLI, or setup behavior changes

### B. Validate Locally
Before each commit:

// turbo
```bash
ruff check .
```

// turbo
```bash
ruff format --check .
```

// turbo
```bash
python -m pytest -q
```

When changing packaging metadata, build config, dependency management, or CLI
entry points, also validate once with:

```bash
python -m build
```

### C. Documentation Drift Check
Before considering the task complete, explicitly check whether the work changed
repository-operational documentation, including:

- `README.md`
- `docs/`
- `AGENTS.md`
- `.agent/rules/`, especially `architecture-context.md` and `tech-stack.md`
- `.agent/workflows/`

If the task changed API contracts, CLI behavior, setup commands, testing
strategy, CI behavior, release flow, or repo governance expectations, update the
affected documentation in the same task or explicitly state that it was checked
and no change was needed.

### D. Commit Changes
Use Conventional Commits format.

```bash
git add <files>
git commit -m "type: description"
```

**Commit Types:**
- `feat:` New feature
- `fix:` Bug fix
- `refactor:` Code restructuring
- `test:` Test additions/changes
- `docs:` Documentation only
- `chore:` Maintenance tasks

**CRITICAL:** Always ask the user to confirm before running `git commit`. Present:
- Files to be committed
- Proposed commit message
- Summary of changes

---

## Notes
- **Always** run lint + format-check + tests before committing
- **Prefer** matching CI's Python 3.14 baseline locally
- **Add a build check** when packaging or release-relevant surfaces changed
- **Always** ask user before committing
- Commit frequently with meaningful messages
- Keep branches short-lived (ideally < 1 day)
- Never commit directly to `main`, including `.agent/` changes
- For push/PR submission, use the `/pr-submit` workflow
