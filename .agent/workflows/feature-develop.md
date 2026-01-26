---
description: Create a feature branch and develop a new feature following git-workflow.md
---

# Feature Branch Development Workflow

This workflow guides you through the complete lifecycle of developing a new feature on a dedicated branch.

> [!IMPORTANT]
> **Agent Rules Compliance**: During development, you MUST strictly follow ALL rules defined in `.agent/rules/`:
> - `python-style.md` - Code style (f-strings, naming, sorting, logging)
> - `python-docs.md` - Documentation (Google Style docstrings)
> - `teck-stack.md` - Technology choices (Python 3.12+, strict typing, pathlib)
> - `testing.md` - Test structure (AAA pattern, fixtures, pytest)
> - `architecture.md` - Library architecture (Flat Feature Model)
> - `git-workflow.md` - This workflow itself

## Step 1: Start Clean
Ensure you're working from the latest main branch.

```bash
git checkout main
git pull
```

## Step 2: Create Feature Branch
Create a new branch with a descriptive name using the `feature/` prefix.

```bash
git checkout -b feature/<descriptive-name>
```

**Naming Convention:**
- Use kebab-case (lowercase with hyphens)
- Be specific: `feature/add-user-authentication` not `feature/auth`
- Keep it short but meaningful

## Step 3: Develop (The Loop)
Iterate on your feature with frequent validation.

### A. Write Code
- Modify files in `src/` for implementation
- Add/update tests in `tests/`

### B. Validate Locally
Before each commit:

// turbo
```bash
ruff format .
```

// turbo
```bash
python3 -m pytest
```

### C. Commit Changes
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
- **Always** run format + tests before committing
- **Always** ask user before committing
- Commit frequently with meaningful messages
- Keep branches short-lived (ideally < 1 day)
- Never commit directly to `main` (except `.agent/` changes)
- For push/PR submission, use the `/pr-submit` workflow
