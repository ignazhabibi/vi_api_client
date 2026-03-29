---
trigger: always_on
---

# Git & Development Workflow

Strict guidelines for version control and feature development.

## 1. Core Principle: The "Main Is Sacred" Rule

- **Never** commit directly to `main`.
- Treat pull requests as the default path for all changes, including
  documentation and `.agent/` guidance updates.
- Only bypass the PR path if the user explicitly asks for an emergency shortcut
  and confirms the tradeoff.
- `main` should always be green.
- Keep work on short-lived feature branches.

## 2. Feature Branch Lifecycle

### A. Start Clean

1. `git checkout main`
2. `git pull`
3. `git checkout -b feature/<descriptive-name>`

### B. Develop

1. Make the intended changes in `src/`, `tests/`, `docs/`, or `.agent/`.
2. Run the standard local gates before proposing a commit:
   - `ruff check .`
   - `ruff format --check .`
   - `python -m pytest -q`
3. If the change touches packaging metadata, build config, dependency
   management, or CLI entry points, also validate with:
   - `python -m pip install build`
   - `python -m build`
4. Commit with a Conventional Commit style message.

### C. Submit

1. `git push -u origin feature/<name>`
2. Create or update the pull request.
3. Wait for the repository quality checks to pass.
4. Merge through GitHub using the normal review flow.
5. Delete the branch on GitHub after merge when appropriate.

### D. Re-Sync

1. `git checkout main`
2. `git pull`
3. `git branch -d feature/<name>`

## 3. Release Process

For releases, follow `.agent/workflows/release.md`.

The short version is:

1. Land the version bump through the normal PR flow.
2. Create an **annotated** tag on the merged `main` commit.
3. Push the tag to trigger the release workflow.

Use annotated tags because the GitHub Release body is populated from the tag
message.

## 4. Agent Role and Permissions

- **Allowed**: `git checkout -b`, `git add`, `git commit`, `git push`.
- **Forbidden**: `git merge` outside explicitly requested user-driven flows, or
  direct commits to `main` without an approved emergency bypass.
- **Validation**: Run `ruff check .`, `ruff format --check .`, and
  `python -m pytest -q` before proposing a push. Add `python -m build` when the
  change touches packaging or release-related surfaces.
