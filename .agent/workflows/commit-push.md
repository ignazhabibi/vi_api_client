---
description: Lint, Test, Commit and Push (without release creation)
---

# Commit & Push Workflow

This workflow automates quality checks, commit message generation, and pushing code.

## 1. Quality Assurance

The agent will automatically run these checks in order:

### 1.1 Linting & Static Analysis
Auto-fix style issues:
```bash
// turbo
ruff check --fix .
```
> [!WARNING]
> If `ruff` reports errors that cannot be auto-fixed, the agent will **STOP** and report them.

### 1.2 Unit Tests (Fast)
Run the test suite:
```bash
// turbo
pytest
```
> [!IMPORTANT]
> If tests fail, the agent will **ABORT** and report the failures.

### 1.3 Integration Verification (Mock)
Verify the library with complex data structures:
```bash
// turbo
pytest -m integration
```

## 2. Smart Commit (Agent)

The agent will:
1. **Analyze Changes:** Run `git status` and `git diff` to understand what changed
2. **Generate Message:** Create a conventional commit message based on:
   - File patterns (e.g., `tests/` → `test:`, `docs/` → `docs:`, `.agent/` → `chore:`)
   - Change type (feat, fix, refactor, etc.)
   - Scope and description
3. **Stage & Commit:** Execute `git add .` and `git commit -m "generated message"`

**Example outputs:**
- `test: add hysteresis parsing tests`
- `fix: correct device endpoint URL construction`
- `docs: update testing.md with AAA comment requirements`
- `refactor: rename single-letter variables in test suite`

## 3. Version Bump (Optional)

If the user requests a version bump, the agent will:
1. **Analyze Git History:** Check commits since last tag for semver impact
2. **Update pyproject.toml:** Bump `version` field
3. **Create Tag:** Run `git tag v$VERSION`
4. **Commit:** Use message `chore: bump version to $VERSION`

## 4. Push (Agent)

The agent will push:
```bash
// turbo
git push && git push --tags
```

**Note:** GitHub release creation is done manually or via separate workflow.
