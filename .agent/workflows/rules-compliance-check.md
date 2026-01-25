---
description: Bring the current file up to the standards defined in the project rules
---

# Code Compliance & Modernization

**Goal:** Bring the current file up to the standards defined in the project rules (`.agent/rules/*.md`).

## Instructions

Act as a strict Code Reviewer and Refactoring Agent. Analyze the code and apply the following transformations step-by-step.

### Phase 1: Structure & Imports (ref: `python-style.md`)
1.  **Imports:** Sort all imports using standard sorting logic.
2.  **Sorting:** Sort the contents of Lists (e.g., `SUPPORTED_PLATFORMS`, `CONSTANTS`) and Dictionary keys **alphabetically**.
3.  **Filesystem:** Replace all `os.path` operations with `pathlib.Path` syntax.
4.  **Naming:** Identify short variable names (k, v, d) and rename them to be descriptive (key, value, data).

### Phase 2: Syntax & Logging (CRITICAL!)
1.  **Modern Python (3.12):** Update syntax to Python 3.12 standards (e.g., use `|` for Unions instead of `Optional` or `Union`, use `type` aliases).
2.  **Logging:** Audit EVERY `_LOGGER` call:
    - **MUST FIX:** Convert f-strings (`f"..."`) inside logger calls to Lazy Formatting (`%s`).
    - Remove trailing periods `.` from log messages.
3.  **Strings:** Convert all *other* string concatenations (outside of logging) to **f-strings**.

### Phase 3: Typing & Documentation (ref: `python-docs.md`)
1.  **Typing:**
    - Replace `List`, `Dict`, `Tuple` imports with native types (`list`, `dict`, `tuple`).
    - Remove `Any` where a specific type can be inferred.
2.  **Docstrings:**
    - Ensure a file-header docstring exists.
    - Add docstrings for all Public Methods using **Google Style**.
    - **IMPORTANT:** Remove type definitions from the docstring text (e.g., change `Args: name (str):` to `Args: name:`).

## Output
Apply the changes directly to the file. Afterwards, provide a short bullet-point summary of what was fixed.
