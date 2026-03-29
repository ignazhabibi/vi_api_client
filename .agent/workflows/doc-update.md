---
description: Synchronize repository documentation with current library behavior, tooling, and workflow reality
---

# Documentation Update Workflow

This workflow ensures that repository-operational documentation stays
self-contained and synchronized with the current library implementation,
tooling, and workflow reality.

## 1. Analysis Phase

Identify which parts of the repository changed and which documentation surfaces
must be updated together.

### Check `README.md`
- **Installation**: Are the Python and setup instructions still accurate?
- **Quick Start**: Do the CLI and Python examples still reflect the current API?
- **Features List**: Are new capabilities listed and removed ones deleted?

### Check `docs/`
- **`01_getting_started.md`**: Confirm setup and first-run steps still work.
- **`02_api_structure.md`**: Confirm the flat feature model still matches the
  implementation.
- **`03_auth_reference.md`**: Check auth setup and token handling against
  `auth.py`.
- **`04_models_reference.md`**: Compare against `src/vi_api_client/models.py`.
- **`05_client_reference.md`**: Compare against `src/vi_api_client/api.py`.
- **`06_cli_reference.md`**: Compare CLI commands and flags against
  `src/vi_api_client/cli.py`.
- **`07_exceptions_reference.md`**: Check `src/vi_api_client/exceptions.py`.

### Check `AGENTS.md`
- **Bootstrap Guidance**: Does the entrypoint still name the correct
  always-relevant files and workflows?
- **Operational Truths**: Do setup, CI, release, and repo-boundary notes still
  match reality?

### Check `.agent/rules/`
- **`architecture-context.md`**: Does the documented library contract still
  match the code?
- **`tech-stack.md`**: Does the Python/tooling guidance still match the repo?
- **Other Rules**: Do testing, git, and repo-boundary rules still reflect how
  the project is maintained?

### Check `.agent/workflows/`
- **Workflow Triggers**: Does each workflow still match how work is really done
  in this repository?
- **Validation Steps**: Do local and CI checks in the workflows still match the
  actual commands used by the repo?

## 2. Verification Steps (The "Meticulous Check")

For every documentation surface you update:
1.  **Open the source file** alongside the doc file.
2.  **Verify names:** Class names, method names, CLI arguments, and file names.
3.  **Verify types and signatures:** Check Python signatures against Markdown.
4.  **Verify logic:** If the docs explain behavior, ensure it matches the code,
    workflow, or CI configuration.

> [!IMPORTANT]
> If there is *any* discrepancy, the code is the source of truth. Update the documentation to match the code.

## 3. Execution

1.  Update every affected documentation surface, not just `README.md`.
2.  If public API, CLI behavior, setup instructions, testing strategy, CI flow,
    or release flow changed, update the relevant `.agent` files in the same task.
3.  If code examples are present in docs, test them when practical.
4.  In the final handoff, explicitly state which documentation files were
    checked and which ones needed no change.
