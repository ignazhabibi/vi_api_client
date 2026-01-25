---
description: Perform a safe release (Lint -> Test -> Tag -> Release)
---

# Release Workflow

This workflow guides you through creating a production release. It ensures quality by enforcing linting and testing before tagging.

## 1. Quality Assurance

### Linting
First, auto-fix any style issues.
```bash
ruff check --fix .
```
> [!WARNING]
> If `ruff` reports errors that cannot be auto-fixed, **STOP**. Fix them manually before proceeding.

### Testing
Run the full test suite.
```bash
// turbo
pytest
```
> [!IMPORTANT]
> If tests fail, **ABORT** the release. Do not proceed until all tests pass.

## 2. Version Bump & Notes (Agent)

Command the AI Agent to:
1.  **Analyze Git History:** Check commits since the last tag for `BREAKING CHANGE` (Major), `feat` (Minor), or other (Patch).
2.  **Update Config:** Bump `version` in `pyproject.toml`.
3.  **Generate Notes:** Create `RELEASE_NOTES.md` summarizing changes and warning about breaking changes.

## 3. Git Operations

Authenticate the release (Agent execution):
```bash
# Set VERSION variable from pyproject.toml
VERSION=$(grep -m1 'version =' pyproject.toml | cut -d '"' -f 2)

git add pyproject.toml
git commit -m "chore: release v$VERSION"
git tag v$VERSION
```

## 4. Push & Release

Push the commit and the tag:
```bash
git push && git push origin v$VERSION
```

Create the GitHub release (uses generated notes with warnings):
```bash
gh release create v$VERSION -F RELEASE_NOTES.md
rm RELEASE_NOTES.md
```
*If you don't have the `gh` CLI tool, go to the GitHub repository > Releases > Draft a new release.*
