---
description: lint, test, commit and push, then create Pull Request
---

# Feature Branch PR Workflow

This workflow enforces the "Main is Sacred" rule. It checks your current branch, commits changes, and helps you create a PR.

## 1. Safety Checks (Agent)

The agent must first verify:
1.  **Branch Check**: Ensure we are NOT on `main`.
    ```bash
    git branch --show-current
    ```
    > [!WARNING]
    > If output is `main`, the Agent must create or request a short-lived
    > feature branch before proceeding. Direct `main` commits are not the
    > default path, including documentation and `.agent/` updates.

2.  **Lint & Test**:
    ```bash
    // turbo
    ruff check .
    // turbo
    ruff format --check .
    // turbo
    python -m pytest -q
    ```
    > [!IMPORTANT]
    > If tests fail, **ABORT**.

3.  **Packaging Sanity Check (when relevant)**:
    If the change touches `pyproject.toml`, build config, dependency management,
    or CLI entry points, validate the package build too.
    ```bash
    python -m build
    ```

## 2. Commit (Agent)

1.  **Stage**: Stage the intended files, not unrelated worktree changes.
2.  **Commit**: Generate a comprehensive commit message `type(scope): description`.
    - `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.
    - Keep the subject concise and informative.

## 3. Push & PR (Agent)

1.  **Push**:
    ```bash
    git push -u origin HEAD
    ```

2.  **Create PR**:
    *   **Option A (Best)**: If `gh` CLI is installed:
        ```bash
        gh pr create --fill --web
        ```
    *   **Option B (Fallback)**: If `gh` fails or is missing, **construct and display the URL** for the user:
        `https://github.com/ignazhabibi/vi_api_client/pull/new/<BRANCH_NAME>`

    > [!TIP]
    > Always check if `gh` is available with `gh --version` before trying Option A.

## X. Releases

For release work, use the dedicated workflow in `.agent/workflows/release.md`.
