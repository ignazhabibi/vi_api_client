---
description: analyze changes, bump version, generate changelog, and tag release
---

# Release Workflow

This workflow guides the agent through creating a semantic release for the
library package.

## 1. Pre-Flight Checks
1.  **Branch Check**: Ensure `main` is fully up-to-date.
    ```bash
    git checkout main
    git pull
    ```
2.  **Clean State**: `git status` must show no modified files.
3.  **Local Validation**: Run the same quality gates the repo expects before
    cutting the release.
    ```bash
    ruff check .
    ruff format --check .
    python -m pytest -q
    ```
4.  **Build Validation**: Install `build` if needed and verify the package can
    be built.
    ```bash
    python -m pip install build
    python -m build
    ```

## 2. Analysis & Versioning
1.  **Get Current Version**: Read `version` from `pyproject.toml`.
2.  **Analyze Commits**:
    ```bash
    git log --pretty=format:"%h %s" $(git describe --tags --abbrev=0)..HEAD
    ```
    *Note: If no tags exist, just use `git log`.*
3.  **Determine Bump**:
    -   **MAJOR**: Breaking changes (look for `BREAKING CHANGE`, `!:` or explicit notes).
    -   **MINOR**: New features (`feat:`).
    -   **PATCH**: Bug fixes (`fix:`), docs, chores.
4.  **Propose**:
    -   Tell the user the Current Version.
    -   List the changes grouped by type.
    -   Propose the New Version.
    -   **WAIT for Confirmation**.

## 3. Generate Changelog
Create a changelog snippet in the requested style.

**IMPORTANT:** Use the exact commit subject lines from `git log`. The annotated
tag body becomes the GitHub Release body in CI.

```markdown
# Changelog

## Breaking Changes 🚨
<commit_hash> <scope>: <message> (BC)

## New Features 💫
<commit_hash> <scope>: <message>

## Bug Fixes 🐞
<commit_hash> <scope>: <message>

## Other Changes ☀️
<commit_hash> <scope>: <message>
```

## 4. Execution
1.  **Create Release Branch**:
    ```bash
    git checkout -b release/v<NEW_VERSION>
    ```
2.  **Bump Version**: Edit `pyproject.toml` with the new version.
3.  **Commit**:
    ```bash
    git add pyproject.toml
    git commit -m "chore(release): bump version to <NEW_VERSION>"
    ```
4.  **Push Release Branch**:
    ```bash
    git push -u origin release/v<NEW_VERSION>
    ```
5.  **Open and Merge Release PR**:
    - Open a PR from `release/v<NEW_VERSION>` into `main`.
    - Wait for the normal quality checks to pass.
    - Merge the PR through GitHub.
6.  **Refresh Local Main**:
    ```bash
    git checkout main
    git pull
    ```
7.  **Tag the Merged Main Commit**:
    ```bash
    git -c core.commentChar=";" tag -a v<NEW_VERSION> -m "Release v<NEW_VERSION>" -m "<PASTE_CHANGELOG_HERE>"
    git push origin v<NEW_VERSION>
    ```

## 5. Post-Release
- GitHub Actions will build and publish the release from the tag run.
- Do not claim the release is complete until the tag workflow is green.
- If `gh` is available, verify recent runs explicitly:
  ```bash
  gh run list --workflow ci_cd.yml --limit 5
  ```
- Perform a documentation drift check for release-related guidance:
  - `README.md`
  - `docs/`
  - `AGENTS.md`
  - `.agent/rules/tech-stack.md`
  - `.agent/rules/git-workflow.md`
  - `.agent/workflows/release.md`
