---
description: analyze changes, bump version, generate changelog, and tag release
---

# Release Workflow

This workflow guides the agent to create a semantic release.

## 1. Pre-Flight Checks
1.  **Branch Check**: Ensure we are on `main` and fully up-to-date.
    ```bash
    git checkout main && git pull
    ```
2.  **Clean State**: `git status` must show no modified files.

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
Create a changelog snippet in the requested style:

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
1.  **Bump Version**: Edit `pyproject.toml` with the new version.
2.  **Commit**:
    ```bash
    git add pyproject.toml
    git commit -m "chore(release): bump version to <NEW_VERSION>"
    ```
3.  **Tag**:
    ```bash
    git -c core.commentChar=";" tag -a v<NEW_VERSION> -m "Release v<NEW_VERSION>" -m "<PASTE_CHANGELOG_HERE>"
    ```
4.  **Push**:
    ```bash
    git push origin main
    git push origin v<NEW_VERSION>
    ```

## 5. Post-Release
- GitHub Actions will automatically build and publish the release.
- The Agent should notify the user that the release is live.
