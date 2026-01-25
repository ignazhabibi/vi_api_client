---
description: Synchronize documentation (README + /docs) with the codebase
---

# Documentation Update Workflow

This workflow ensures that `README.md` and all files in `docs/` are strictly synchronized with the implementation.

## 1. Analysis Phase

Identify which components have changed and require documentation updates.

### Check `README.md`
- Verify **Installation Steps**: Are dependencies up to date?
- Verify **Quick Start**: does the example code still run?
- Verify **Features List**: Are new features listed? Are removed features deleted?

### Check `docs/`
- **`04_models_reference.md`**: Compare against `src/vi_api_client/models.py`. Ensure all fields and types match exactly.
- **`05_client_reference.md`**: Compare against `src/vi_api_client/client.py`. Ensure method signatures (params, return types) are identical.
- **`06_cli_reference.md`**: Run `vi-client --help` and compare output against documentation.
- **`07_exceptions_reference.md`**: Check `src/vi_api_client/exceptions.py`.

## 2. Verification Steps (The "Meticulous Check")

For *every* piece of code documentation you update:
1.  **Open the source file** alongside the doc file.
2.  **Verify names:** Function names, Class names, Variable names.
3.  **Verify types:** Check type hints in Python vs described types in Markdown.
4.  **Verify logic:** If the docs explain *how* something works, ensure readability matches the code's logic.

> [!IMPORTANT]
> If there is *any* discrepancy, the code is the source of truth. Update the documentation to match the code.

## 3. Execution

1.  Make the necessary edits to `.md` files.
2.  If code examples are present in docs, **test them** (e.g., by running them in a temporary script) to ensure they work.
3.  Commit changes: `git commit -m "docs: update reference documentation"`
